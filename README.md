# award-search-mcp

A read-only MCP server that lets an AI agent answer award seat questions using the Seats.aero partner API. See `PLAN.md` for decisions and progress, and `GLOSSARY.md` for terms.

## Setup

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m pytest
```

Tests run offline against saved responses in `tests/fixtures/`.
