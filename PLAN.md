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
- **Results are capped:** sorted by fewest points per seat, top 20, with a count of how many were left out. When there are more than 20, a summary of all matches is added. It's grouped by route and date, or by route and month (with the dates) for ranges over 31 days, so the cap can't hide whole dates or airports. Added November 14, 2026, after evals showed Claude needing 3–4 searches to see past the cap; with the summary it usually needs one.
- **Each result shows:** date, route, departure time, loyalty program, operating airline(s), points per seat and in total, taxes and fees in the program's own currency, seats available, stops and the connection airports, whether it's mixed cabin, last seen, and a Seats.aero link.
- **Identical searches within 15 minutes reuse the earlier result** and say so, to save API quota. Failed searches aren't reused. Added November 14, 2026, after evals showed Claude sometimes sending the same search twice at once. On Lambda (stage 7) each running copy has its own cache, so this is best-effort there: two identical searches at the same moment can use 2 API calls. Accepted, since I'm the only user and the quota is 1,000 calls a day.
- **Data can be out of date.** Every result shows when it was last seen, and the output tells the agent to suggest confirming on the loyalty program's site before transferring points.
- **The API key stays on the server** and never reaches the model.
- **Python with the official MCP Python SDK.** Tests use saved fake API responses and never hit the network.
- **Fixtures are made up.** Seats.aero's terms (updated August 12, 2026) say flights more than 60 days out can't be shown publicly, and this repo is public. So test and eval fixtures are invented flights in Seats.aero's response format, and real responses are only used locally to learn the format. History was rewritten on November 14, 2026 to remove the real responses committed before this rule.
- **Runs on my laptop first.** AWS hosting is an optional final stage if costs stay low.
- **Hosted on AWS Lambda with a function URL** (stage 7, decided November 14, 2026). Deployed with an AWS SAM template to `us-west-2`. Secrets live in Parameter Store. Every request needs a bearer token, which the server checks itself instead of using the SDK's OAuth settings. Those settings make the server announce OAuth, and an open Claude Code bug report says that makes it ignore the token header. Only Claude Code uses the remote server: Claude Desktop accepts a static token only through a beta limited to some organizations, so it keeps using the local version. The URL isn't committed, because this repo is public: Claude Code is set up with `claude mcp add --scope user`.

## Stages

### 1. Set up the project
- [x] Python virtual environment, pytest, `.gitignore` (including `.DS_Store`).
- [x] Copy a sample Seats.aero response from the monitor's `tests/fixtures/` as the first test fixture.

**Done when:** `python -m pytest` runs (even with zero tests) on a fresh clone.

**Write-up:** Added a `.venv` virtual environment on Python 3.12, a `requirements.txt` pinning `pytest==9.2.0`, a `.gitignore` (venv, caches, `.DS_Store`, `.env`) and a README with setup steps. The first fixture, `tests/fixtures/lhr_lax_match.json`, is copied byte-for-byte from the monitor. I added one small test that loads it, because pytest treats "no tests found" as a failure (exit code 5), and a real passing test is clearer evidence. I checked "fresh clone" by copying only the files git would commit into a scratch folder, building a new venv there and running `python -m pytest`: 1 passed.

### 2. Build the search core
- [x] A `search()` function that takes a route search and returns a result with a clear status (found, nothing found, failed).
- [x] Adapt the monitor's per-flight filtering: cabin, seats, points limit, direct only.
- [x] Sorting, the 20-result cap and the "showing 20 of N" count.

**Done when:** tests pass for a match, no match, an API failure, a mixed cabin result and the cap.

**Write-up:** `award_search/search.py` has `search()`, which takes a `RouteSearch` and returns a `SearchResult` with status found, nothing found or failed. A failed result always includes a reason, and the reason never contains the API key. The per-flight filtering is adapted from the monitor, but the cabin, seats, points limit and direct only values now come from the route search instead of constants. The HTTP call uses Python's built-in `urllib`, so no dependency was added. The tests fake the network call and cover a match, no match, API failures (401, 429, 500, unreachable, bad JSON, missing key), a mixed cabin flight, direct only and the 20-result cap. One surprise: the current Seats.aero docs have a per-flight `MixedCabinPct` field. The search sends `min_cabin_pct=0` so mixed cabin flights are returned and labelled with that field instead of being hidden. The mixed cabin fixture is hand-written from the docs, because no real response with that field was saved.

### 3. Build one MCP tool
- [x] Check the current MCP Python SDK docs for stdio servers and tool definitions (not from memory).
- [x] Expose `search_award_seats` over stdio.

**Done when:** a test starts the server, lists its tools and calls `search_award_seats` against a fixture.

**Write-up:** Added `mcp==2.3.0`, the current stable SDK. Version 2 renamed the server class from `FastMCP` to `MCPServer`, so most older examples online no longer match. `award_search/server.py` defines `search_award_seats` and runs over stdio with `python -m award_search.server`. The SDK builds the tool's input schema from type hints, and the descriptions come from `Field(...)`, so bad arguments like `cabin: "coach"` are rejected before any search runs. The tool returns a `SearchAnswer` with a status, the seats, "shown" and "total found" counts, and a plain-language message. The message says a failed search doesn't mean there are no seats, and that found seats should be confirmed before transferring points. The main test starts the server as a real subprocess and points it at a fake Seats.aero on `127.0.0.1` via a new `SEATS_AERO_API_URL` setting. It checks that the API key reaches Seats.aero but never appears in the tool result. Surprise: the SDK doesn't pass your environment variables to a subprocess server by default, so the test passes the key in explicitly.

### 4. Connect it to Claude
- [x] Add the server to Claude Desktop or Claude Code.
- [x] Try vague prompts ("anything from Nice in mid-July?", "business to Tokyo sometime in spring") and adjust the tool description when Claude picks wrong arguments.

**Done when:** vague prompts reliably lead to sensible tool calls and answers.

**Write-up:** The server is added to Claude Code with a project `.mcp.json`. It reads `${SEATS_AERO_API_KEY}` from the shell, which loads it from the macOS Keychain, so the key isn't stored in any file. I ran 7 prompts through `claude -p` against the real API. They were the plan's examples, a follow-up answer in the same conversation, a repeat for consistency, a new "2 of us to Paris next fall, nonstop" prompt, and one with a fake key. Every tool call was sensible, so the tool description didn't need changes. Claude turned "mid-July" into July 10–20, "Tokyo" into HND+NRT and "2 of us" into `seats: 2`. When a prompt was missing an origin or destination, it asked instead of guessing. With the fake key it said "I couldn't check" and not "no seats". The surprise came from real data, before any prompt testing. American reports 0 remaining seats on almost every available flight (1,708 of 1,720 trips), meaning the count is unknown, so the search was hiding nearly all American results. Those flights are now included with an unknown seat count. Taxes reported as 0 are shown as unknown, and departure times drop a misleading `Z`, because they're local time. The Seats.aero link format, copied from the monitor, was checked by hand: it opens the right search.

### 5. Handle failures
- [x] A bad API key, an empty result, old data and an oversized response.

**Done when:** each case has a test, and the output makes the failure or data age clear enough that the agent can't present a failed check as "no seats".

**Write-up:** `tests/test_failures.py` covers each case through the real MCP tool and checks the message the agent sees.
- **Bad key:** I first checked what Seats.aero really sends for a bad key (HTTP 401 with `bad_partner_key`), so that test uses the real response.
- **Old data:** each result now has `hours_since_seen`. The message flags results last seen more than 48 hours ago. I picked 48 hours from real data: the median result was 13.5 hours old, about a sixth were over 48 hours, and none were over a week.
- **Oversized:** a response with `hasMore` is marked `complete: false`, and the message says "at least N" and "not a complete check". This includes the case where nothing was found but Seats.aero had more results. A 1,000-item response still gives a 20-result answer of about 13,000 characters, well under Claude Code's 10,000-token warning. A real 7 MB response took only 1.2 seconds, so the 20-second timeout is fine.
- **Surprise:** a response without a `data` list was being reported as "nothing found", the exact problem this stage is about. It's now a failure.

A real broad search through `claude -p` showed Claude passing on the new warnings, for example "last seen about 5 days ago, so it's the most likely to be gone".

### 6. Add evals
- [x] Write 5-10 test prompts covering vague dates, no availability and a Seats.aero outage, plus a way to rerun them.

**Done when:** the prompts can be rerun after a change and the results compared.

**Write-up:** `evals/` has 8 cases: vague dates with a points limit, a missing destination, cities and party size, a season, no availability, an outage, a bad key and old data. `python -m evals.run_evals --repeat 3` runs each case through `claude -p` against a fake Seats.aero, saves the results in `evals/results/` and shows what changed since the last run. Each case runs in an empty folder with only our tool available, so this repo's CLAUDE.md can't steer it, and the server gets a made-up key, so evals never use the real API. Most checks are plain code on the tool arguments. Four rules that code can't judge reliably, such as "doesn't present a failure as no seats", are graded by a second Claude call (Haiku). The first run found real variation: for "anything from Nice in mid-July?", Claude sometimes guessed 12 destinations instead of asking. I added "ask instead of guessing airports" to the tool description, and it then passed 3/3, with all 8 cases passing 3/3. To check that the evals can actually fail, I temporarily made failed searches report "no award seats". Claude still said it couldn't check, because it also saw `status: failed`, but it lost the reason, and the bad key case failed as it should.

### 7. Host it on AWS (optional)
- [x] Check that the Seats.aero terms and API quota allow this use. Checked November 14, 2026: personal, non-commercial use is allowed without permission; a Pro subscription "may not be shared with others"; the quota is 1,000 API calls per day; and data must be credited to Seats.aero with a link. Hosting is fine only if I'm the sole user.
- [x] Estimate the monthly cost before deploying. Checked November 14, 2026 against AWS's pricing pages: about $0.01/month for normal use (~300 searches) and under $1/month even at the full Seats.aero quota every day, before any free tier. Assumes Arm, 512 MB and ~2 seconds per search. Parameter Store standard parameters are free. Junk requests to the public URL are the main cost risk (about $0.25 per million), so reserved concurrency limits how many copies run at once, and the account's existing $5 monthly budget emails me. Ruled out: Lightsail ($3.50–5/month, plus a domain for HTTPS), App Runner (closed to new customers since April 30, 2026) and ECS/Fargate (needs a load balancer).
- [x] Host it over HTTP with authentication.

**Done when:** Claude can use the remote server and unauthenticated requests are rejected.

**Write-up:** The server runs on Lambda (Arm, Python 3.12) behind a function URL, and `template.yaml` describes it for AWS SAM. `award_search/http_app.py` serves the unchanged MCP server over HTTP through AWS's Lambda Web Adapter. The server is stateless, so any running copy can answer, and it rejects any request without the bearer token with a 401. It also won't start with a token under 32 characters. On Lambda, `boto3`, which comes with Lambda's Python runtime, loads both secrets from Parameter Store. Evidence: 8 new tests run the HTTP server as its own process (all 49 pass), the live URL returned 401 with no token or a wrong one and 200 with the right one, and `claude -p` ran a real search through it. A `Makefile` packages only the server and `mcp`, so I split `requirements-dev.txt` (which adds pytest) from `requirements.txt`. Surprises: a new copy of the Lambda takes about 2.2 seconds to start and AWS bills that time, while warm requests take 2–4 ms. Committing the URL to `.mcp.json` would have published it, so Claude Code gets it from a user-scope setting instead. And I dropped the budget I'd planned in the template, because the account's existing $5 budget already sends alerts. Follow-up: a new session failed with a bare 401, and Claude guessed "expired token". Capturing the request showed the real cause: when `AWARD_SEARCH_MCP_TOKEN` isn't set, Claude Code sends the literal text `${AWARD_SEARCH_MCP_TOKEN}` as the token. Each 401 now says exactly what was wrong (no header, no token, not "Bearer", an unfilled `${NAME}` variable, or a wrong token), and Claude now tells you which variable to set. In that case Claude also filled in typical points prices from general knowledge (3 of 3 runs). A sentence in the 401 saying no data was retrieved didn't reliably stop it (2 of 3), because Claude Code treats connection errors as information, not instructions. So it was removed. An instruction from me did stop it (0 of 3). When a search itself fails, Claude already doesn't guess, and a new eval check, `no_guesses_after_failure`, keeps it that way.

## Backlog

- **The monitor hides American seats.** `seats-aero-monitor`'s `flight_qualifies` treats `RemainingSeats: 0` as no seats, but for American it means unknown. That's for the owner to decide, since the monitor isn't changed from here.

## Later ideas

- OAuth for the hosted server, so Claude Desktop and claude.ai can use it without the static-token beta.
- Open searches by program ("where can I fly business with Aeroplan in March?"), using Seats.aero's broader availability endpoint.
- Use the Playwright MCP next to this server to check other sites. This only involves configuring Claude, not changing this server's code. Check each site's terms first.
