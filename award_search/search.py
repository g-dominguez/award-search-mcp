"""
Route searches against the Seats.aero partner API.

search() takes a RouteSearch and always returns a SearchResult with one of
three statuses: found, nothing found, or failed. A failed search is never
reported as an empty one.

The per-flight filtering is adapted from seats-aero-monitor.
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import StrEnum

# Overridable so tests can point a running server at a local fake Seats.aero
API_URL = os.getenv("SEATS_AERO_API_URL", "https://seats.aero/partnerapi/search")
RESULT_CAP = 20
# Most rows in the summary of all matches; past this, rows are grouped more coarsely
SUMMARY_CAP = 40
# Results last seen longer ago than this are flagged as old (about the oldest sixth in real data)
STALE_AFTER_HOURS = 48

# Seats.aero prefixes its per-cabin summary fields with these letters (WMileageCost, JAvailable...)
CABIN_LETTERS = {"economy": "Y", "premium": "W", "business": "J", "first": "F"}


class SearchStatus(StrEnum):
    FOUND = "found"
    NOTHING_FOUND = "nothing_found"
    FAILED = "failed"


@dataclass(frozen=True)
class RouteSearch:
    """A question about award seats between given airports, dates, cabin and points limit."""

    origins: list[str]
    destinations: list[str]
    start_date: date
    end_date: date
    cabin: str
    seats: int = 1
    points_limit: int | None = None  # per seat
    direct_only: bool = False
    loyalty_programs: list[str] | None = None  # None means all loyalty programs


@dataclass(frozen=True)
class AwardSeat:
    """One flight with award seats that matches a route search."""

    date: str
    origin: str
    destination: str
    departs_at: str | None  # local time at the origin airport
    loyalty_program: str
    airlines: str
    flight_numbers: str | None
    points_per_seat: int
    points_total: int
    taxes: float | None  # None when Seats.aero doesn't know
    taxes_currency: str | None
    seats_available: int | None  # None when Seats.aero doesn't know the count
    stops: int
    mixed_cabin: bool
    mixed_cabin_pct: int | None  # share of the distance flown below the requested cabin
    last_seen: str | None
    hours_since_seen: int | None
    link: str


@dataclass(frozen=True)
class SummaryRow:
    """The cheapest points per seat and number of matching flights for one route in one period."""

    origin: str
    destination: str
    period: str  # a date (YYYY-MM-DD), a month (YYYY-MM) or "all dates"
    cheapest_points: int
    flights: int
    dates: list[str] | None = None  # dates with matches, for month rows only


@dataclass(frozen=True)
class SearchResult:
    status: SearchStatus
    seats: list[AwardSeat] = field(default_factory=list)
    total_found: int = 0  # before the cap, so "showing 20 of N" is possible
    reason: str | None = None  # only set when the search failed
    complete: bool = True  # False when Seats.aero had more results than it sent
    # Covers every match when there are more than RESULT_CAP, so the cap can't hide whole dates or routes
    summary: list[SummaryRow] = field(default_factory=list)
    summary_by: str | None = None  # "date", "month" or "route"


class SeatsAeroError(Exception):
    """The Seats.aero API couldn't be reached or gave an unusable answer."""


def search(route_search: RouteSearch, api_key: str | None = None, now: datetime | None = None) -> SearchResult:
    """Run a route search and return found seats, nothing found, or failed with a reason."""
    now = now or datetime.now(timezone.utc)
    if api_key is None:
        api_key = os.getenv("SEATS_AERO_API_KEY", "").strip()
    if not api_key:
        return SearchResult(SearchStatus.FAILED, reason="SEATS_AERO_API_KEY is not set.")

    if route_search.cabin not in CABIN_LETTERS:
        return SearchResult(
            SearchStatus.FAILED,
            reason=f"Unknown cabin {route_search.cabin!r}. Use one of: {', '.join(CABIN_LETTERS)}.",
        )

    if route_search.end_date < route_search.start_date:
        return SearchResult(SearchStatus.FAILED, reason="end_date is before start_date.")

    try:
        response = fetch_availability(build_params(route_search), api_key)
    except SeatsAeroError as e:
        return SearchResult(SearchStatus.FAILED, reason=str(e))

    items = response.get("data")
    if not isinstance(items, list):
        # Without this, an unexpected answer would look like "nothing found"
        return SearchResult(SearchStatus.FAILED, reason="Seats.aero sent a response without a list of results.")
    complete = not response.get("hasMore", False)

    matches = find_award_seats(items, route_search, now)
    if not matches:
        return SearchResult(SearchStatus.NOTHING_FOUND, complete=complete)

    matches.sort(key=lambda s: (s.points_per_seat, s.date, s.departs_at or ""))
    summary, summary_by = summarize(matches, route_search) if len(matches) > RESULT_CAP else ([], None)
    return SearchResult(
        SearchStatus.FOUND,
        seats=matches[:RESULT_CAP],
        total_found=len(matches),
        complete=complete,
        summary=summary,
        summary_by=summary_by,
    )


def summarize(matches: list[AwardSeat], route_search: RouteSearch) -> tuple[list[SummaryRow], str]:
    """Group every match by route and period. Uses dates for ranges up to 31 days, otherwise months,
    and switches to coarser groups when there would be more than SUMMARY_CAP rows."""
    days_searched = (route_search.end_date - route_search.start_date).days + 1
    levels = (["date"] if days_searched <= 31 else []) + ["month", "route"]
    period_of = {"date": lambda s: s.date, "month": lambda s: s.date[:7], "route": lambda s: "all dates"}

    for level in levels:
        groups: dict[tuple[str, str, str], list[AwardSeat]] = {}
        for seat in matches:
            groups.setdefault((seat.origin, seat.destination, period_of[level](seat)), []).append(seat)
        if len(groups) <= SUMMARY_CAP or level == "route":
            rows = [
                SummaryRow(
                    origin,
                    destination,
                    period,
                    min(s.points_per_seat for s in seats),
                    len(seats),
                    sorted({s.date for s in seats}) if level == "month" else None,
                )
                for (origin, destination, period), seats in sorted(groups.items())
            ]
            return rows, level


def build_params(route_search: RouteSearch) -> dict:
    params = {
        "origin_airport": ",".join(route_search.origins),
        "destination_airport": ",".join(route_search.destinations),
        "start_date": route_search.start_date.isoformat(),
        "end_date": route_search.end_date.isoformat(),
        "cabins": route_search.cabin,
        "only_direct_flights": "true" if route_search.direct_only else "false",
        # 0 lets mixed cabin flights through, so they can be labelled instead of hidden
        "min_cabin_pct": 0,
        "include_trips": "true",
        "take": 1000,
    }
    if route_search.loyalty_programs:
        params["sources"] = ",".join(route_search.loyalty_programs)
    return params


def fetch_availability(params: dict, api_key: str) -> dict:
    """Call the Seats.aero cached search. Raises SeatsAeroError with a reason the agent can show."""
    url = f"{API_URL}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(
        url, headers={"Partner-Authorization": api_key, "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read()
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise SeatsAeroError(f"Seats.aero rejected the API key (HTTP {e.code}).") from e
        if e.code == 429:
            raise SeatsAeroError("Seats.aero rate limit reached (HTTP 429). Try again later.") from e
        raise SeatsAeroError(f"Seats.aero returned an error (HTTP {e.code}).") from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise SeatsAeroError("Couldn't reach Seats.aero. Check the internet connection.") from e

    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        raise SeatsAeroError("Seats.aero sent a response that isn't valid JSON.") from e
    if not isinstance(data, dict):
        raise SeatsAeroError("Seats.aero sent a response in an unexpected format.")
    return data


def find_award_seats(items: list[dict], route_search: RouteSearch, now: datetime) -> list[AwardSeat]:
    """Turn availability items into award seats. Each flight is checked on its own."""
    letter = CABIN_LETTERS[route_search.cabin]
    matches = []

    for item in items:
        if not item.get(f"{letter}Available", False):
            continue

        trips = item.get("AvailabilityTrips")
        if not trips:
            # No flight-level data from this loyalty program: treat the summary as one flight
            trips = [{
                "RemainingSeats": item.get(f"{letter}RemainingSeats"),
                "MileageCost": item.get(f"{letter}MileageCost"),
                "Carriers": item.get(f"{letter}Airlines"),
                "Stops": 0 if item.get(f"{letter}Direct") else 1,
            }]

        for trip in trips:
            if flight_qualifies(trip, route_search):
                matches.append(to_award_seat(item, trip, route_search, now))

    return matches


def parse_points(raw) -> int:
    """Points can come as an int or a string. Returns 0 if missing or unreadable."""
    return int(raw) if str(raw).isdigit() else 0


def flight_qualifies(trip: dict, route_search: RouteSearch) -> bool:
    """Check one flight against the cabin, seats, points limit and direct only criteria."""
    cabin = trip.get("Cabin")
    if cabin and cabin != route_search.cabin:
        return False

    # Some loyalty programs (mostly American) report 0 for "count unknown", so 0 isn't treated as none left
    seats = int(trip.get("RemainingSeats") or 0)
    if seats and seats < route_search.seats:
        return False

    points = parse_points(trip.get("MileageCost"))
    if points == 0:
        return False
    if route_search.points_limit is not None and points > route_search.points_limit:
        return False

    if route_search.direct_only and int(trip.get("Stops") or 0) > 0:
        return False

    return True


def hours_since(timestamp: str | None, now: datetime) -> int | None:
    """Whole hours between a Seats.aero timestamp (UTC) and now, or None if it's missing or unreadable."""
    try:
        seen = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None
    return int((now - seen).total_seconds() // 3600)


def to_award_seat(item: dict, trip: dict, route_search: RouteSearch, now: datetime) -> AwardSeat:
    route = item.get("Route") or {}
    origin = trip.get("OriginAirport") or route.get("OriginAirport", "")
    destination = trip.get("DestinationAirport") or route.get("DestinationAirport", "")
    flight_date = item.get("Date", "")
    points = parse_points(trip.get("MileageCost"))
    mixed_cabin_pct = trip.get("MixedCabinPct")
    taxes = int(trip.get("TotalTaxes") or item.get("TotalTaxes") or 0)
    departs_at = trip.get("DepartsAt")
    last_seen = trip.get("UpdatedAt") or item.get("UpdatedAt")

    return AwardSeat(
        date=flight_date,
        origin=origin,
        destination=destination,
        # Seats.aero marks times with Z, but they're local to each airport (duration only adds up that way)
        departs_at=departs_at.removesuffix("Z") if departs_at else None,
        loyalty_program=item.get("Source") or route.get("Source", ""),
        airlines=trip.get("Carriers") or "",
        flight_numbers=trip.get("FlightNumbers"),
        points_per_seat=points,
        points_total=points * route_search.seats,
        # Seats.aero gives taxes times 100 in every currency, including JPY. 0 means unknown.
        taxes=taxes / 100 if taxes else None,
        taxes_currency=trip.get("TaxesCurrency") or item.get("TaxesCurrency"),
        seats_available=int(trip.get("RemainingSeats") or 0) or None,
        stops=int(trip.get("Stops") or 0),
        mixed_cabin=bool(mixed_cabin_pct),
        mixed_cabin_pct=mixed_cabin_pct,
        last_seen=last_seen,
        hours_since_seen=hours_since(last_seen, now),
        link=f"https://seats.aero/search?origin={origin}&destination={destination}&date={flight_date}",
    )
