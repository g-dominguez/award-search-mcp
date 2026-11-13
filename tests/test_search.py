import copy
import io
import json
import urllib.error
from datetime import date
from pathlib import Path

import pytest

from award_search import search as search_module
from award_search.search import RESULT_CAP, RouteSearch, SearchStatus, search

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name):
    return json.loads((FIXTURES / name).read_text())


class FakeResponse(io.BytesIO):
    """Stands in for what urlopen returns: something with read() that works in a with block."""


@pytest.fixture
def fake_api(monkeypatch):
    """Replace the network call. Call the returned function with the JSON Seats.aero should send."""
    calls = []

    def respond_with(data):
        def fake_urlopen(request, timeout):
            calls.append(request)
            return FakeResponse(json.dumps(data).encode())

        monkeypatch.setattr(search_module.urllib.request, "urlopen", fake_urlopen)
        return calls

    return respond_with


def route_search(**overrides):
    defaults = dict(
        origins=["LHR"],
        destinations=["LAX"],
        start_date=date(2027, 7, 17),
        end_date=date(2027, 7, 17),
        cabin="premium",
        seats=2,
        points_limit=75000,
    )
    return RouteSearch(**(defaults | overrides))


def test_match_returns_qualifying_flights_cheapest_first(fake_api):
    fake_api(load_fixture("lhr_lax_match.json"))

    result = search(route_search(), api_key="fake-key")

    assert result.status == SearchStatus.FOUND
    # AA135 has 0 seats left, so only the two Virgin Atlantic flights match
    assert [s.flight_numbers for s in result.seats] == ["VS7", "VS23"]
    assert result.total_found == 2

    cheapest = result.seats[0]
    assert cheapest.points_per_seat == 55000
    assert cheapest.points_total == 110000
    assert cheapest.taxes == 493.63
    assert cheapest.taxes_currency == "GBP"
    assert cheapest.seats_available == 8
    assert cheapest.loyalty_program == "virginatlantic"
    assert cheapest.departs_at == "2027-07-17T09:55:00Z"
    assert cheapest.last_seen == "2026-11-06T18:24:13.784173Z"
    assert cheapest.stops == 0
    assert not cheapest.mixed_cabin


def test_request_sends_key_in_header_and_search_in_url(fake_api):
    calls = fake_api(load_fixture("empty.json"))

    search(route_search(origins=["LHR", "LGW"], loyalty_programs=["aeroplan"]), api_key="fake-key")

    request = calls[0]
    assert request.get_header("Partner-authorization") == "fake-key"
    assert "fake-key" not in request.full_url
    assert "origin_airport=LHR%2CLGW" in request.full_url
    assert "sources=aeroplan" in request.full_url
    assert "min_cabin_pct=0" in request.full_url


def test_points_limit_is_per_seat(fake_api):
    fake_api(load_fixture("lhr_lax_match.json"))

    result = search(route_search(points_limit=56000), api_key="fake-key")

    assert [s.flight_numbers for s in result.seats] == ["VS7"]


def test_seats_needed_filters_out_flights_with_too_few(fake_api):
    fake_api(load_fixture("lhr_lax_match.json"))

    result = search(route_search(seats=9), api_key="fake-key")

    assert [s.flight_numbers for s in result.seats] == ["VS23"]


def test_no_match_is_nothing_found_not_failed(fake_api):
    fake_api(load_fixture("empty.json"))

    result = search(route_search(), api_key="fake-key")

    assert result.status == SearchStatus.NOTHING_FOUND
    assert result.seats == []
    assert result.reason is None


def test_flights_filtered_out_is_nothing_found(fake_api):
    fake_api(load_fixture("lhr_lax_match.json"))

    result = search(route_search(points_limit=10000), api_key="fake-key")

    assert result.status == SearchStatus.NOTHING_FOUND


@pytest.mark.parametrize(
    "error, expected_reason",
    [
        (urllib.error.HTTPError("url", 401, "Unauthorized", {}, None), "rejected the API key"),
        (urllib.error.HTTPError("url", 429, "Too Many Requests", {}, None), "rate limit"),
        (urllib.error.HTTPError("url", 500, "Server Error", {}, None), "HTTP 500"),
        (urllib.error.URLError("no route to host"), "Couldn't reach Seats.aero"),
    ],
)
def test_api_failure_is_failed_with_reason(monkeypatch, error, expected_reason):
    def failing_urlopen(request, timeout):
        raise error

    monkeypatch.setattr(search_module.urllib.request, "urlopen", failing_urlopen)

    result = search(route_search(), api_key="secret-key")

    assert result.status == SearchStatus.FAILED
    assert expected_reason in result.reason
    assert "secret-key" not in result.reason


def test_invalid_json_is_failed(monkeypatch):
    monkeypatch.setattr(
        search_module.urllib.request, "urlopen", lambda request, timeout: FakeResponse(b"<html>")
    )

    result = search(route_search(), api_key="fake-key")

    assert result.status == SearchStatus.FAILED
    assert "valid JSON" in result.reason


def test_missing_api_key_is_failed(monkeypatch):
    monkeypatch.delenv("SEATS_AERO_API_KEY", raising=False)

    result = search(route_search())

    assert result.status == SearchStatus.FAILED
    assert "SEATS_AERO_API_KEY" in result.reason


def test_mixed_cabin_flight_is_included_and_labelled(fake_api):
    fake_api(load_fixture("nce_lax_mixed_cabin.json"))

    result = search(route_search(origins=["NCE"]), api_key="fake-key")

    assert result.status == SearchStatus.FOUND
    flight = result.seats[0]
    assert flight.mixed_cabin
    assert flight.mixed_cabin_pct == 9
    assert flight.stops == 1
    assert (flight.origin, flight.destination) == ("NCE", "LAX")


def test_direct_only_excludes_flights_with_stops(fake_api):
    fake_api(load_fixture("nce_lax_mixed_cabin.json"))

    result = search(route_search(origins=["NCE"], direct_only=True), api_key="fake-key")

    assert result.status == SearchStatus.NOTHING_FOUND


def test_results_are_capped_with_total_count(fake_api):
    response = load_fixture("lhr_lax_match.json")
    item = response["data"][0]
    template = item["AvailabilityTrips"][0]
    # 25 flights priced 74000, 73000 ... 50000, so the cheapest 20 are known
    item["AvailabilityTrips"] = [
        copy.deepcopy(template) | {"FlightNumbers": f"VS{n}", "MileageCost": 50000 + n * 1000}
        for n in range(24, -1, -1)
    ]
    fake_api(response)

    result = search(route_search(), api_key="fake-key")

    assert result.status == SearchStatus.FOUND
    assert len(result.seats) == RESULT_CAP
    assert result.total_found == 25
    points = [s.points_per_seat for s in result.seats]
    assert points == sorted(points)
    assert points[0] == 50000
    assert points[-1] == 69000


def test_end_date_before_start_date_is_failed():
    result = search(route_search(start_date=date(2027, 7, 20), end_date=date(2027, 7, 17)), api_key="fake-key")

    assert result.status == SearchStatus.FAILED
    assert "end_date" in result.reason
