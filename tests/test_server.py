import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from mcp import Client, StdioServerParameters

from award_search.server import mcp

REPO_ROOT = Path(__file__).parent.parent
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def fake_seats_aero():
    """A local HTTP server that answers every request with the LHR to LAX fixture."""
    received_keys = []
    body = (FIXTURES / "lhr_lax_match.json").read_bytes()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            received_keys.append(self.headers.get("Partner-Authorization"))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass  # keep test output quiet

    server = HTTPServer(("127.0.0.1", 0), Handler)  # port 0 picks any free port
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/partnerapi/search", received_keys
    server.shutdown()


@pytest.mark.anyio
async def test_server_lists_and_runs_search_award_seats_over_stdio(fake_seats_aero):
    url, received_keys = fake_seats_aero
    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "award_search.server"],
        cwd=REPO_ROOT,
        env={"SEATS_AERO_API_KEY": "fake-key", "SEATS_AERO_API_URL": url},
    )

    async with Client(server) as client:
        tools = await client.list_tools()
        assert [tool.name for tool in tools.tools] == ["search_award_seats"]

        result = await client.call_tool(
            "search_award_seats",
            {
                "origins": ["lhr"],
                "destinations": ["LAX"],
                "start_date": "2027-07-17",
                "end_date": "2027-07-17",
                "cabin": "premium",
                "seats": 2,
                "points_limit": 75000,
            },
        )

    assert not result.is_error
    answer = result.structured_content
    assert answer["status"] == "found"
    assert answer["shown"] == 2
    assert answer["total_found"] == 2
    assert [s["flight_numbers"] for s in answer["seats"]] == ["VS7", "VS23"]
    assert answer["seats"][0]["points_total"] == 110000
    assert "Showing 2 of 2" in answer["message"]
    assert "last_seen" in answer["message"]
    # The key reached Seats.aero but not the agent
    assert received_keys == ["fake-key"]
    assert "fake-key" not in str(result)


@pytest.mark.anyio
async def test_invalid_arguments_are_a_tool_error():
    async with Client(mcp) as client:
        result = await client.call_tool(
            "search_award_seats",
            {
                "origins": ["LHR"],
                "destinations": ["LAX"],
                "start_date": "2027-07-17",
                "end_date": "2027-07-17",
                "cabin": "coach",
            },
        )

    assert result.is_error
