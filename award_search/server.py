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

from award_search.search import AwardSeat, RouteSearch, SearchStatus, search

AirportCode = Annotated[str, Field(pattern=r"^[A-Za-z]{3}$", description="3-letter IATA airport code.")]

STALE_DATA_NOTE = (
    "Seats.aero data can be out of date: check each result's last_seen, and suggest "
    "confirming on the loyalty program's site before transferring points."
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
    "mid-July" into a date range before calling. Results are sorted by fewest points
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

    if result.status == SearchStatus.FAILED:
        message = f"The search failed: {result.reason} This does not mean there are no seats."
    elif result.status == SearchStatus.NOTHING_FOUND:
        message = "Seats.aero has no award seats matching this search. The search ran successfully."
    else:
        message = (
            f"Showing {len(result.seats)} of {result.total_found} flights, fewest points per seat first. "
            + STALE_DATA_NOTE
        )

    return SearchAnswer(
        status=result.status,
        message=message,
        seats=result.seats,
        shown=len(result.seats),
        total_found=result.total_found,
    )


if __name__ == "__main__":
    mcp.run()
