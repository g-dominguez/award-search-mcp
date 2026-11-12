# Plan: Award search MCP server

**Goal:** let an AI agent answer questions about award seats on request, such as "any premium economy seats from Nice to LA in mid-July under 75k?"

**Who it's for:** me, from Claude Desktop or Claude Code. It's also a portfolio piece, so the code should be clean enough to explain to an employer.

**Approach:** one stage at a time. Move on only when the "Done when" line is true. Each finished stage gets a short "Write-up:" under it.

Terms used here are defined in `GLOSSARY.md`.

## Decisions

- **Standalone repo.** Search and filtering code is copied from `seats-aero-monitor` and adapted, not shared. The monitor keeps watching the July 2027 trip and isn't changed.
- **Read-only.** The server answers questions. It never books, sends alerts, saves searches or handles points transfers.
- **Seats.aero only** for now, using my existing API key.
- **Route searches only** in version 1: airports, date range, cabin, number of seats, points limit.
- **Claude does the interpreting.** It turns "mid-July" into exact dates and "London" into `LHR, LGW`. The tool only takes exact dates and airport codes.
- **Points limit is per seat.** Results also show the total for all seats.
- **Flights with stops and mixed cabins are included** and clearly labelled. A "direct only" option is available.
- **All loyalty programs by default**, and a search can be narrowed to some of them.
- **A failed search never looks like an empty one.** Every search returns one of: found seats, found nothing, or failed (with the reason).
- **Results are capped:** sorted by fewest points per seat, top 20, with a count of how many were left out.
- **Each result shows:** date, route, departure time, loyalty program, operating airline(s), points per seat and in total, taxes and fees in the program's own currency, seats available, stops, whether it's mixed cabin, last seen, and a Seats.aero link.
- **Data can be out of date.** Every result shows when it was last seen, and the output tells the agent to suggest confirming on the loyalty program's site before transferring points.
- **The API key stays on the server** and never reaches the model.
- **Python with the official MCP Python SDK.** Tests use saved fake API responses and never hit the network.
- **Runs on my laptop first.** AWS hosting is an optional final stage if costs stay low.

## Stages

### 1. Set up the project
- [x] Python virtual environment, pytest, `.gitignore` (including `.DS_Store`).
- [x] Copy a sample Seats.aero response from the monitor's `tests/fixtures/` as the first test fixture.

**Done when:** `python -m pytest` runs (even with zero tests) on a fresh clone.

**Write-up:** Added a `.venv` virtual environment on Python 3.12, a `requirements.txt` pinning `pytest==9.2.0`, a `.gitignore` (venv, caches, `.DS_Store`, `.env`) and a README with setup steps. The first fixture, `tests/fixtures/lhr_lax_match.json`, is copied byte-for-byte from the monitor. I added one small test that loads it, because pytest treats "no tests found" as a failure (exit code 5), and a real passing test is clearer evidence. I checked "fresh clone" by copying only the files git would commit into a scratch folder, building a new venv there and running `python -m pytest`: 1 passed.

### 2. Build the search core
- [ ] A `search()` function that takes a route search and returns a result with a clear status (found, nothing found, failed).
- [ ] Adapt the monitor's per-flight filtering: cabin, seats, points limit, direct only.
- [ ] Sorting, the 20-result cap and the "showing 20 of N" count.

**Done when:** tests pass for a match, no match, an API failure, a mixed cabin result and the cap.

### 3. Build one MCP tool
- [ ] Check the current MCP Python SDK docs for stdio servers and tool definitions (not from memory).
- [ ] Expose `search_award_seats` over stdio.

**Done when:** a test starts the server, lists its tools and calls `search_award_seats` against a fixture.

### 4. Connect it to Claude
- [ ] Add the server to Claude Desktop or Claude Code.
- [ ] Try vague prompts ("anything from Nice in mid-July?", "business to Tokyo sometime in spring") and adjust the tool description when Claude picks wrong arguments.

**Done when:** vague prompts reliably lead to sensible tool calls and answers.

### 5. Handle failures
- [ ] A bad API key, an empty result, old data and an oversized response.

**Done when:** each case has a test, and the output makes the failure or data age clear enough that the agent can't present a failed check as "no seats".

### 6. Add evals
- [ ] Write 5-10 test prompts covering vague dates, no availability and a Seats.aero outage, plus a way to rerun them.

**Done when:** the prompts can be rerun after a change and the results compared.

### 7. Host it on AWS (optional)
- [ ] Check that the Seats.aero terms and API quota allow this use.
- [ ] Estimate the monthly cost before deploying.
- [ ] Host it over HTTP with authentication.

**Done when:** Claude can use the remote server and unauthenticated requests are rejected.

## Later ideas

- Open searches by program ("where can I fly business with Aeroplan in March?"), using Seats.aero's broader availability endpoint.
- Use the Playwright MCP next to this server to check other sites. This only involves configuring Claude, not changing this server's code. Check each site's terms first.
