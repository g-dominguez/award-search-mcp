"""
MCP server exposing award seat route searches as one tool, search_award_seats.

Run it over stdio with: python -m award_search.server
The Seats.aero API key is read from SEATS_AERO_API_KEY and never appears in tool output.
"""

from dataclasses import dataclass
from datetime import date
from typing import Annotated, Literal

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from award_search.search import STALE_AFTER_HOURS, AwardSeat, RouteSearch, SearchResult, SearchStatus, search

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


INCOMPLETE_NOTE = (
    "Seats.aero had more results than fit in one response, so this is not a complete check. "
    "Narrow the search (fewer airports or a shorter date range) to see everything."
)


def describe(result: SearchResult) -> str:
    """A plain-language summary that keeps failed, incomplete and old results from looking like answers."""
    if result.status == SearchStatus.FAILED:
        return f"The search failed: {result.reason} This does not mean there are no seats."

    if result.status == SearchStatus.NOTHING_FOUND:
        if not result.complete:
            return "No matching award seats in the results Seats.aero sent. " + INCOMPLETE_NOTE
        return "Seats.aero has no award seats matching this search. The search ran successfully."

    total = f"{result.total_found}" if result.complete else f"at least {result.total_found}"
    parts = [f"Showing {len(result.seats)} of {total} flights, fewest points per seat first."]
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
    flying from or to, ask them instead of guessing airports. Results are sorted by fewest points
    per seat and capped at 20. Flights with stops and mixed cabin flights (part of the
    distance in a lower cabin) are included and labelled. A status of "failed" means
    the search did not run, so never report it as "no seats".
    """
    result = search(
        RouteSearch(
            origins=[code.upper() for code in origins],
            destinations=[code.upper() for code in destinations],
            start_date=start_date,
            end_date=end_date,
            cabin=cabin,
            seats=seats,
            points_limit=points_limit,
            direct_only=direct_only,
            loyalty_programs=loyalty_programs,
        )
    )

    return SearchAnswer(
        status=result.status,
        message=describe(result),
        seats=result.seats,
        shown=len(result.seats),
        total_found=result.total_found,
        complete=result.complete,
    )


if __name__ == "__main__":
    mcp.run()
