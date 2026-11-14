"""
A fake Seats.aero for evals: answers searches from one saved response (filtered to the
requested airports and dates, like the real API) or with one error.

Last-seen times are rewritten relative to now on each request, so a fixture stays
"fresh" (or "old") no matter when the evals run.
"""

import json
import threading
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


@dataclass(frozen=True)
class Scenario:
    fixture: Path | None = None  # saved response to send with HTTP 200
    status: int = 200
    error_body: str = ""  # sent instead of a fixture when status isn't 200
    hours_since_seen: int = 3  # how long ago every result was last seen


def matching(response: dict, query: str) -> dict:
    """Keep only results for the requested airports and dates, as the real API does."""
    params = urllib.parse.parse_qs(query)
    origins = set(params.get("origin_airport", [""])[0].split(","))
    destinations = set(params.get("destination_airport", [""])[0].split(","))
    start, end = params.get("start_date", [""])[0], params.get("end_date", ["9999"])[0]
    response["data"] = [
        item for item in response.get("data", [])
        if item["Route"]["OriginAirport"] in origins
        and item["Route"]["DestinationAirport"] in destinations
        and start <= item["Date"] <= end
    ]
    response["count"] = len(response["data"])
    return response


def with_last_seen(response: dict, hours_ago: int) -> dict:
    seen = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat().replace("+00:00", "Z")
    for item in response.get("data", []):
        item["UpdatedAt"] = seen
        for trip in item.get("AvailabilityTrips") or []:
            trip["UpdatedAt"] = seen
    return response


class FakeSeatsAero:
    """Use as a context manager: `with FakeSeatsAero(scenario) as url:`"""

    def __init__(self, scenario: Scenario):
        self.scenario = scenario
        self.requests = []  # query strings received, for debugging

    def __enter__(self) -> str:
        scenario, requests = self.scenario, self.requests

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append(self.path)
                if scenario.status != 200:
                    body, content_type = scenario.error_body.encode(), "text/plain"
                else:
                    response = json.loads(scenario.fixture.read_text())
                    response = matching(response, urllib.parse.urlparse(self.path).query)
                    response = with_last_seen(response, scenario.hours_since_seen)
                    body, content_type = json.dumps(response).encode(), "application/json"
                self.send_response(scenario.status)
                self.send_header("Content-Type", content_type)
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{self.server.server_port}/partnerapi/search"

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
