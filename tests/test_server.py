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
    assert [s["flight_numbers"] for s in answer["seats"]] == ["VS9007", "VS9023"]
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


LHR_LAX_ARGS = {
    "origins": ["LHR", "LGW"],
    "destinations": ["LAX"],
    "start_date": "2027-07-17",
    "end_date": "2027-07-17",
    "cabin": "premium",
}


@pytest.fixture
def counting_api(monkeypatch):
    """Fake Seats.aero that counts calls. Set .fail_next = True to make the next call an HTTP 500."""
    import io
    import json
    import urllib.error

    from award_search import search as search_module

    class Api:
        calls = 0
        fail_next = False

    def fake_urlopen(request, timeout):
        Api.calls += 1
        if Api.fail_next:
            Api.fail_next = False
            raise urllib.error.HTTPError(request.full_url, 500, "Server Error", {}, None)
        return io.BytesIO((FIXTURES / "lhr_lax_match.json").read_bytes())

    monkeypatch.setattr(search_module.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setenv("SEATS_AERO_API_KEY", "fake-key")
    return Api


@pytest.mark.anyio
async def test_identical_search_reuses_the_earlier_result(counting_api):
    async with Client(mcp) as client:
        first = await client.call_tool("search_award_seats", LHR_LAX_ARGS)
        # Same search with the airports in another order and lower case
        second = await client.call_tool("search_award_seats", LHR_LAX_ARGS | {"origins": ["lgw", "LHR"]})

    assert counting_api.calls == 1
    assert second.structured_content["seats"] == first.structured_content["seats"]
    assert second.structured_content["message"].startswith("This is the same search as one made less than a minute ago")
    assert "same search" not in first.structured_content["message"]


@pytest.mark.anyio
async def test_a_different_search_calls_seats_aero_again(counting_api):
    async with Client(mcp) as client:
        await client.call_tool("search_award_seats", LHR_LAX_ARGS)
        await client.call_tool("search_award_seats", LHR_LAX_ARGS | {"seats": 2})

    assert counting_api.calls == 2


@pytest.mark.anyio
async def test_a_failed_search_is_not_reused(counting_api):
    counting_api.fail_next = True
    async with Client(mcp) as client:
        failed = await client.call_tool("search_award_seats", LHR_LAX_ARGS)
        retry = await client.call_tool("search_award_seats", LHR_LAX_ARGS)

    assert failed.structured_content["status"] == "failed"
    assert retry.structured_content["status"] == "found"
    assert counting_api.calls == 2


@pytest.mark.anyio
async def test_reuse_ends_after_15_minutes(counting_api, monkeypatch):
    from award_search import server

    now = [1000.0]
    monkeypatch.setattr(server, "clock", lambda: now[0])
    async with Client(mcp) as client:
        await client.call_tool("search_award_seats", LHR_LAX_ARGS)
        now[0] += 14 * 60
        reused = await client.call_tool("search_award_seats", LHR_LAX_ARGS)
        now[0] += 2 * 60  # 16 minutes after the first search
        await client.call_tool("search_award_seats", LHR_LAX_ARGS)

    assert "made 14 min ago" in reused.structured_content["message"]
    assert counting_api.calls == 2


def test_identical_searches_at_the_same_time_make_one_api_call(monkeypatch):
    """Claude sometimes sends the same search twice at once. The second should wait for the first."""
    import io
    import threading
    import time
    from datetime import date

    from award_search import search as search_module
    from award_search import server
    from award_search.search import RouteSearch

    calls = []

    def slow_urlopen(request, timeout):
        calls.append(request)
        time.sleep(0.3)  # long enough for the second search to arrive while the first is running
        return io.BytesIO((FIXTURES / "lhr_lax_match.json").read_bytes())

    monkeypatch.setattr(search_module.urllib.request, "urlopen", slow_urlopen)
    monkeypatch.setenv("SEATS_AERO_API_KEY", "fake-key")
    route_search = RouteSearch(["LHR"], ["LAX"], date(2027, 7, 17), date(2027, 7, 17), "premium")

    answers = []
    threads = [threading.Thread(target=lambda: answers.append(server.cached_search(route_search))) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(calls) == 1
    assert sorted(minutes is None for _, minutes in answers) == [False, True]  # one ran, one reused it
