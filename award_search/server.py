"""
MCP server exposing award seat route searches as one tool, search_award_seats.

Run it over stdio with: python -m award_search.server
The Seats.aero API key is read from SEATS_AERO_API_KEY and never appears in tool output.
"""

import threading
import time
from dataclasses import dataclass
from datetime import date
from typing import Annotated, Literal

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from award_search.search import (
    STALE_AFTER_HOURS,
    AwardSeat,
    RouteSearch,
    SearchResult,
    SearchStatus,
    SummaryRow,
    search,
)

AirportCode = Annotated[str, Field(pattern=r"^[A-Za-z]{3}$", description="3-letter IATA airport code.")]

STALE_DATA_NOTE = (
    "Seats.aero data can be out of date: check each result's last_seen, and suggest "
    "confirming on the loyalty program's site before transferring points. "
    "Departure times are local to the origin airport. A null seats_available or taxes "
    "means Seats.aero doesn't know it, so say so rather than guessing."
)

mcp = MCPServer(
    "award-search",
    instructions="Read-only award seat search using Seats.aero. It never books or transfers points.",
)


@dataclass
class SearchAnswer:
    """What the agent gets back from search_award_seats."""

    status: SearchStatus
    message: str
    seats: list[AwardSeat]
    shown: int
    total_found: int
    complete: bool  # False means Seats.aero had more results than it sent
    summary: list[SummaryRow]  # every match by route and period, when there are more than 20


# An identical search within this time returns the earlier result instead of using another API call
CACHE_SECONDS = 15 * 60
_cache: dict[str, tuple[float, SearchResult]] = {}
# One lock per distinct search: an identical search waits for the one already running, others don't
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()
clock = time.monotonic  # replaceable in tests


def clear_cache() -> None:
    _cache.clear()
    _locks.clear()


def cached_search(route_search: RouteSearch) -> tuple[SearchResult, int | None]:
    """Run the search, or reuse an identical one from the last 15 minutes.
    Returns the result and, when reused, how many minutes ago it ran. Failures aren't kept, so a retry really retries."""
    key = repr(route_search)
    with _locks_guard:
        lock = _locks.setdefault(key, threading.Lock())
    with lock:
        if key in _cache:
            ran_at, result = _cache[key]
            if clock() - ran_at < CACHE_SECONDS:
                return result, int((clock() - ran_at) // 60)
        result = search(route_search)
        if result.status != SearchStatus.FAILED:
            _cache[key] = (clock(), result)
        return result, None


INCOMPLETE_NOTE = (
    "Seats.aero had more results than fit in one response, so this is not a complete check. "
    "Narrow the search (fewer airports or a shorter date range) to see everything."
)


def describe(result: SearchResult, repeated_minutes_ago: int | None = None) -> str:
    """A plain-language summary that keeps failed, incomplete and old results from looking like answers."""
    if result.status == SearchStatus.FAILED:
        return f"The search failed: {result.reason} This does not mean there are no seats."

    if repeated_minutes_ago is not None:
        when = "less than a minute ago" if repeated_minutes_ago == 0 else f"{repeated_minutes_ago} min ago"
        repeat_note = (
            f"This is the same search as one made {when}, so this is that result again and no new check was "
            "made. Repeating it won't change the answer. "
        )
        return repeat_note + describe(result)

    if result.status == SearchStatus.NOTHING_FOUND:
        if not result.complete:
            return "No matching award seats in the results Seats.aero sent. " + INCOMPLETE_NOTE
        return "Seats.aero has no award seats matching this search. The search ran successfully."

    total = f"{result.total_found}" if result.complete else f"at least {result.total_found}"
    parts = [f"Showing {len(result.seats)} of {total} flights, fewest points per seat first."]
    if result.summary:
        grouping = "route" if result.summary_by == "route" else f"route and {result.summary_by}"
        parts.append(
            f"The summary covers all {result.total_found} flights by {grouping}, with the cheapest points "
            "per seat, number of flights and (for months) the dates in each, so use it for dates and routes "
            "beyond the ones shown."
        )
    if not result.complete:
        parts.append(INCOMPLETE_NOTE)
    old = sum(1 for s in result.seats if s.hours_since_seen is not None and s.hours_since_seen > STALE_AFTER_HOURS)
    if old:
        parts.append(
            f"{old} of these {'was' if old == 1 else 'were'} last seen more than {STALE_AFTER_HOURS} hours ago "
            "(see hours_since_seen), "
            "so they are more likely to be gone."
        )
    parts.append(STALE_DATA_NOTE)
    return " ".join(parts)


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True, open_world_hint=True))
def search_award_seats(
    origins: Annotated[list[AirportCode], Field(min_length=1, description="Origin airports, e.g. [\"LHR\", \"LGW\"] for London.")],
    destinations: Annotated[list[AirportCode], Field(min_length=1, description="Destination airports.")],
    start_date: Annotated[date, Field(description="First departure date to search, YYYY-MM-DD.")],
    end_date: Annotated[date, Field(description="Last departure date to search, YYYY-MM-DD. Same as start_date for one day.")],
    cabin: Annotated[
        Literal["economy", "premium", "business", "first"],
        Field(description="Cabin. \"premium\" means premium economy."),
    ],
    seats: Annotated[int, Field(ge=1, le=9, description="Number of seats needed on the same flight.")] = 1,
    points_limit: Annotated[int | None, Field(ge=1, description="Most points per seat (not total). Omit for no limit.")] = None,
    direct_only: Annotated[bool, Field(description="Only non-stop flights.")] = False,
    loyalty_programs: Annotated[
        list[str] | None,
        Field(description="Seats.aero program names to limit to, e.g. [\"aeroplan\", \"flyingblue\"]. Omit for all."),
    ] = None,
) -> SearchAnswer:
    """Search award seats (flights bookable with points or miles) between airports in a date range.

    Takes exact airport codes and dates only: turn "London" into ["LHR", "LGW"] and
    "mid-July" into a date range before calling. If the user hasn't said where they're
    flying from or to, ask them instead of guessing airports. Don't repeat a search with the
    same arguments: an identical search within 15 minutes returns the earlier result. Results are sorted by fewest points
    per seat and capped at 20, with a summary of all matches by route and date or month
    when there are more. Flights with stops and mixed cabin flights (part of the
    distance in a lower cabin) are included and labelled. A status of "failed" means
    the search did not run, so never report it as "no seats".
    """
    # Sorted and deduplicated, so the same search with airports in another order counts as identical
    result, repeated_minutes_ago = cached_search(
        RouteSearch(
            origins=sorted({code.upper() for code in origins}),
            destinations=sorted({code.upper() for code in destinations}),
            start_date=start_date,
            end_date=end_date,
            cabin=cabin,
            seats=seats,
            points_limit=points_limit,
            direct_only=direct_only,
            loyalty_programs=sorted(set(loyalty_programs)) if loyalty_programs else None,
        )
    )

    return SearchAnswer(
        status=result.status,
        message=describe(result, repeated_minutes_ago),
        seats=result.seats,
        shown=len(result.seats),
        total_found=result.total_found,
        complete=result.complete,
        summary=result.summary,
    )


if __name__ == "__main__":
    mcp.run()
