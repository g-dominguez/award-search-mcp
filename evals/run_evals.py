"""
Run the eval cases through `claude -p` against a fake Seats.aero, score them and compare with the last run.

Usage, from the repo root:
    .venv/bin/python -m evals.run_evals                 # all cases
    .venv/bin/python -m evals.run_evals old_data ...    # only these cases
    .venv/bin/python -m evals.run_evals --repeat 3      # each case 3 times; it passes only if all do

Each case runs in an empty temporary folder (so this repo's CLAUDE.md doesn't steer Claude),
with built-in tools turned off and only the award-search server available. The server gets a
made-up API key and talks to the fake Seats.aero, so evals never touch the real API.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from evals.cases import CASES, Case, CodeCheck, JudgeCheck, Run
from evals.fake_seats_aero import FakeSeatsAero

REPO_ROOT = Path(__file__).parent.parent
RESULTS_DIR = Path(__file__).parent / "results"
TOOL_NAME = "mcp__award-search__search_award_seats"
JUDGE_MODEL = "haiku"
PARALLEL_CASES = 4


def claude_env() -> dict:
    """The environment for `claude -p`, minus the real API key so it can't leak into an eval."""
    return {k: v for k, v in os.environ.items() if k != "SEATS_AERO_API_KEY"}


def run_claude(case: Case) -> tuple[Run, str]:
    """Run one prompt with only our server available. Returns what Claude did and which model ran."""
    fake = FakeSeatsAero(case.scenario)
    with fake as url, tempfile.TemporaryDirectory() as workdir:
        mcp_config = Path(workdir) / "mcp.json"
        mcp_config.write_text(json.dumps({"mcpServers": {"award-search": {
            "command": str(REPO_ROOT / ".venv" / "bin" / "python"),
            "args": ["-m", "award_search.server"],
            "env": {
                "PYTHONPATH": str(REPO_ROOT),
                "SEATS_AERO_API_KEY": "eval-fake-key",
                "SEATS_AERO_API_URL": url,
            },
        }}}))
        proc = subprocess.run(
            ["claude", "-p", case.prompt, "--output-format", "stream-json", "--verbose",
             "--mcp-config", str(mcp_config), "--strict-mcp-config",
             "--tools", "", "--allowedTools", TOOL_NAME, "--no-session-persistence"],
            cwd=workdir, env=claude_env(), capture_output=True, text=True, timeout=600,
        )

    calls, answer, model = [], "", "unknown"
    for line in proc.stdout.splitlines():
        event = json.loads(line)
        if event.get("type") == "system" and event.get("subtype") == "init":
            model = event.get("model", model)
        elif event.get("type") == "assistant":
            calls += [b["input"] for b in event["message"]["content"]
                      if b.get("type") == "tool_use" and b.get("name") == TOOL_NAME]
        elif event.get("type") == "result":
            answer = event.get("result") or ""
    if proc.returncode and not answer:
        answer = f"[claude exited with code {proc.returncode}: {proc.stderr[-300:]}]"
    return Run(calls=calls, answer=answer, api_calls=len(fake.requests)), model


def judge(check: JudgeCheck, case: Case, run: Run) -> tuple[bool, str]:
    """Ask a second Claude call whether the answer follows the rule. Returns (passed, its reason)."""
    prompt = (
        "You are grading an AI assistant's answer about award flight availability.\n\n"
        f"User asked: {case.prompt}\n\n"
        f"Rule: {check.rule}\n\n"
        f"Assistant's answer:\n<answer>\n{run.answer}\n</answer>\n\n"
        "Does the answer follow the rule? Reply with PASS or FAIL on the first line, "
        "then one sentence explaining why."
    )
    proc = subprocess.run(
        ["claude", "-p", prompt, "--model", JUDGE_MODEL, "--tools", "", "--strict-mcp-config",
         "--no-session-persistence"],
        cwd=tempfile.gettempdir(), env=claude_env(), capture_output=True, text=True, timeout=300,
    )
    verdict, _, reason = proc.stdout.strip().partition("\n")
    return verdict.strip().upper().startswith("PASS"), reason.strip() or proc.stderr.strip()[-200:]


def run_once(case: Case) -> dict:
    started = time.monotonic()
    run, model = run_claude(case)
    checks = []
    for check in case.checks:
        if isinstance(check, CodeCheck):
            passed, detail = check.test(run), ""
        else:
            passed, detail = judge(check, case, run)
        checks.append({"name": check.name, "passed": passed, "detail": detail})
    return {
        "passed": all(c["passed"] for c in checks),
        "checks": checks,
        "tool_calls": run.calls,
        "api_calls": run.api_calls,
        "answer": run.answer,
        "model": model,
        "seconds": round(time.monotonic() - started, 1),
    }


def previous_results() -> dict | None:
    files = sorted(RESULTS_DIR.glob("*.json"))
    return json.loads(files[-1].read_text()) if files else None


def print_report(results: dict, previous: dict | None) -> None:
    before = {c["name"]: c for c in previous["cases"]} if previous else {}
    print(f"\nModel: {results['model']}   Judge: {JUDGE_MODEL}")
    for case in results["cases"]:
        mark = "PASS" if case["passed"] else "FAIL"
        runs = case["runs"]
        change = ""
        if case["name"] in before and before[case["name"]]["passed"] != case["passed"]:
            change = "  (was " + ("PASS" if before[case["name"]]["passed"] else "FAIL") + ")"
        searches = ",".join(str(len(r["tool_calls"])) for r in runs)
        api_calls = ",".join(str(r.get("api_calls", "?")) for r in runs)
        print(f"{mark}  {case['name']}  {sum(r['passed'] for r in runs)}/{len(runs)} runs{change}"
              f"  [searches {searches}; API calls {api_calls}]")
        for i, run in enumerate(runs, 1):
            for check in run["checks"]:
                if not check["passed"]:
                    print(f"        x run {i}: {check['name']}  {check['detail']}")
    passed = sum(c["passed"] for c in results["cases"])
    line = f"\n{passed}/{len(results['cases'])} cases passed"
    if previous:
        line += f" (last run {previous['started_at']}: {sum(c['passed'] for c in previous['cases'])}/{len(previous['cases'])})"
    print(line)


def main(args: list[str]) -> int:
    repeat = 1
    if "--repeat" in args:
        i = args.index("--repeat")
        repeat = int(args[i + 1])
        args = args[:i] + args[i + 2:]
    names = args
    cases = [c for c in CASES if not names or c.name in names]
    unknown = set(names) - {c.name for c in CASES}
    if unknown:
        print(f"Unknown case(s): {', '.join(sorted(unknown))}")
        return 2

    previous = previous_results()
    started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"Running {len(cases)} case(s), {repeat} time(s) each...")
    with ThreadPoolExecutor(PARALLEL_CASES) as pool:
        runs = list(pool.map(run_once, [case for case in cases for _ in range(repeat)]))

    case_results = []
    for n, case in enumerate(cases):
        case_runs = runs[n * repeat:(n + 1) * repeat]
        case_results.append({"name": case.name, "passed": all(r["passed"] for r in case_runs), "runs": case_runs})

    results = {
        "started_at": started_at,
        "model": runs[0]["model"],
        "judge_model": JUDGE_MODEL,
        "repeat": repeat,
        "cases": case_results,
    }
    RESULTS_DIR.mkdir(exist_ok=True)
    out = RESULTS_DIR / f"{started_at.replace(' ', '_').replace(':', '')}.json"
    out.write_text(json.dumps(results, indent=2) + "\n")
    print_report(results, previous)
    print(f"Saved to {out.relative_to(REPO_ROOT)}")
    return 0 if all(c["passed"] for c in case_results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
