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

## Run the server

```sh
export SEATS_AERO_API_KEY=...   # your Seats.aero partner API key
python -m award_search.server
```

It talks MCP over stdio, so it's meant to be started by an MCP client such as Claude Desktop or Claude Code rather than used directly.
