import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from award_search import server

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def empty_search_cache():
    """Each test starts with no remembered searches, so one test's results can't leak into another."""
    server.clear_cache()
    yield
    server.clear_cache()


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

    http_server = HTTPServer(("127.0.0.1", 0), Handler)  # port 0 picks any free port
    threading.Thread(target=http_server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{http_server.server_port}/partnerapi/search", received_keys
    http_server.shutdown()
