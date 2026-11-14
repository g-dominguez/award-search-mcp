"""Stage 5: what the agent sees when a search fails, finds nothing, returns old data or is oversized."""

import copy
import io
import json
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from mcp import Client

from award_search import search as search_module
from award_search.search import RouteSearch, hours_since, search
from award_search.server import mcp

FIXTURES = Path(__file__).parent / "fixtures"

LHR_LAX = {
    "origins": ["LHR"],
    "destinations": ["LAX"],
    "start_date": "2027-07-17",
    "end_date": "2027-07-17",
    "cabin": "premium",
}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def api_key(monkeypatch):
    monkeypatch.setenv("SEATS_AERO_API_KEY", "secret-key")


def load_fixture(name):
    return json.loads((FIXTURES / name).read_text())


def respond_with(monkeypatch, data):
    def fake_urlopen(request, timeout):
        return io.BytesIO(json.dumps(data).encode())

    monkeypatch.setattr(search_module.urllib.request, "urlopen", fake_urlopen)


async def call_tool(arguments=LHR_LAX):
    """Call search_award_seats through the MCP server. Returns (structured answer, full text the agent sees)."""
    async with Client(mcp) as client:
        result = await client.call_tool("search_award_seats", arguments)
    assert not result.is_error
    return result.structured_content, result.content[0].text


@pytest.mark.anyio
async def test_bad_api_key_is_a_failure_not_no_seats(monkeypatch):
    # Seats.aero's real answer to a wrong key: HTTP 401 with the body "bad_partner_key"
    def rejecting_urlopen(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, io.BytesIO(b"bad_partner_key"))

    monkeypatch.setattr(search_module.urllib.request, "urlopen", rejecting_urlopen)

    answer, text = await call_tool()

    assert answer["status"] == "failed"
    assert answer["seats"] == []
    assert "rejected the API key" in answer["message"]
    assert "does not mean there are no seats" in answer["message"]
    assert "secret-key" not in text


@pytest.mark.anyio
async def test_empty_result_says_the_search_ran(monkeypatch):
    respond_with(monkeypatch, load_fixture("empty.json"))

    answer, _ = await call_tool()

    assert answer["status"] == "nothing_found"
    assert answer["complete"] is True
    assert "ran successfully" in answer["message"]


@pytest.mark.anyio
async def test_response_without_results_is_a_failure_not_empty(monkeypatch):
    respond_with(monkeypatch, {"error": "something went wrong"})

    answer, _ = await call_tool()

    assert answer["status"] == "failed"
    assert "does not mean there are no seats" in answer["message"]


@pytest.mark.anyio
async def test_old_results_are_flagged(monkeypatch):
    now = datetime.now(timezone.utc)
    response = load_fixture("lhr_lax_match.json")
    vs7, vs23 = response["data"][0]["AvailabilityTrips"]
    vs7["UpdatedAt"] = (now - timedelta(hours=72)).isoformat().replace("+00:00", "Z")
    vs23["UpdatedAt"] = (now - timedelta(hours=2)).isoformat().replace("+00:00", "Z")
    response["data"][1]["UpdatedAt"] = vs23["UpdatedAt"]  # American's AA9135, also fresh
    respond_with(monkeypatch, response)

    answer, _ = await call_tool()

    hours = {s["flight_numbers"]: s["hours_since_seen"] for s in answer["seats"]}
    assert hours["VS9007"] in (72, 73)  # a few seconds pass between building the fixture and the search
    assert hours["VS9023"] in (2, 3)
    assert "1 of these was last seen more than 48 hours ago" in answer["message"]


@pytest.mark.anyio
async def test_fresh_results_are_not_flagged(monkeypatch):
    response = load_fixture("lhr_lax_match.json")
    fresh = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    for item in response["data"]:
        item["UpdatedAt"] = fresh
    respond_with(monkeypatch, response)

    answer, _ = await call_tool()

    assert "48 hours" not in answer["message"]


def test_hours_since_seen_counts_whole_hours():
    now = datetime(2026, 11, 8, 18, 24, 13, tzinfo=timezone.utc)

    assert hours_since("2026-11-06T18:24:13.784173Z", now) == 47  # just under 48 hours
    assert hours_since("2026-11-06T18:24:12Z", now) == 48
    assert hours_since(None, now) is None
    assert hours_since("not a date", now) is None


@pytest.mark.anyio
async def test_more_results_than_one_response_is_marked_incomplete(monkeypatch):
    response = load_fixture("lhr_lax_match.json")
    response["hasMore"] = True
    respond_with(monkeypatch, response)

    answer, _ = await call_tool()

    assert answer["status"] == "found"
    assert answer["complete"] is False
    assert "at least" in answer["message"]
    assert "not a complete check" in answer["message"]


@pytest.mark.anyio
async def test_nothing_found_in_an_incomplete_response_is_not_no_seats(monkeypatch):
    respond_with(monkeypatch, {"data": [], "count": 0, "hasMore": True})

    answer, _ = await call_tool()

    assert answer["status"] == "nothing_found"
    assert answer["complete"] is False
    assert "not a complete check" in answer["message"]
    assert "ran successfully" not in answer["message"]


@pytest.mark.anyio
async def test_oversized_response_still_gives_a_small_answer(monkeypatch):
    # 1,000 availability items with 5 flights each, as big as one Seats.aero response can get
    response = load_fixture("lhr_lax_match.json")
    template = response["data"][0]
    items = []
    for n in range(1000):
        item = copy.deepcopy(template)
        trip = item["AvailabilityTrips"][0]
        item["AvailabilityTrips"] = [trip | {"FlightNumbers": f"VS{n}-{k}", "MileageCost": 50000 + n + k} for k in range(5)]
        items.append(item)
    respond_with(monkeypatch, {"data": items, "count": 1000, "hasMore": False})

    answer, text = await call_tool()

    assert answer["shown"] == 20
    assert answer["total_found"] == 5000
    assert answer["summary"] == [
        {"origin": "LHR", "destination": "LAX", "period": "2027-07-17", "cheapest_points": 50000, "flights": 5000,
         "dates": None}
    ]
    assert "The summary covers all 5000 flights by route and date" in answer["message"]
    # Claude Code warns above 10,000 tokens of tool output; a token is roughly 4 characters
    assert len(text) < 40_000
