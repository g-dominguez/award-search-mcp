# award-search-mcp

A read-only MCP server that lets an AI agent answer award seat questions using the Seats.aero partner API. `PLAN.md` has the decisions and stages, and `GLOSSARY.md` has the terms. Use the glossary's words in code, docs and conversation.

## How to work with me

- I'm new to software development. Explain new concepts briefly the first time they come up, in plain language.
- Write and edit the code yourself. I review the diffs.
- Work on the current stage in `PLAN.md`. Add other problems you find to a "Backlog" section in `PLAN.md` and keep the current change focused.
- A stage is done when its "Done when" line is true and you've shown me the evidence (test output, a command and its result).
- When a stage is done, add a short write-up under it in `PLAN.md`: 3-5 sentences on what changed, the key decisions and anything surprising.
- Check MCP SDK and Seats.aero API details against the current docs, not memory.
- Ask before changing a decision in `PLAN.md` or adding dependencies.
- Commit and push only when I ask.

## Project notes

- `SEATS_AERO_API_KEY` comes from an environment variable. Keep it out of logs, commits and tool output.
- Tests run offline against saved responses in `tests/fixtures/`.
- Search and filtering logic is adapted from my `seats-aero-monitor` repo (`../seats-aero-monitor/seats_aero_monitor.py`). Copy from it and leave that repo unchanged, since it's watching a real trip.
