import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

REPO_ROOT = Path(__file__).parent.parent
TOKEN = "test-token-that-is-long-enough-0123456789"

INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}},
}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_http_server(env: dict[str, str]) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-m", "award_search.http_app"],
        cwd=REPO_ROOT,
        env={"PATH": os.environ["PATH"], **env},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


@pytest.fixture
def http_server(fake_seats_aero):
    """The HTTP server running as its own process, pointed at the fake Seats.aero."""
    api_url, received_keys = fake_seats_aero
    port = free_port()
    process = start_http_server(
        {
            "MCP_BEARER_TOKEN": TOKEN,
            "SEATS_AERO_API_KEY": "fake-key",
            "SEATS_AERO_API_URL": api_url,
            "PORT": str(port),
        }
    )
    deadline = time.monotonic() + 10
    while True:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            if process.poll() is not None or time.monotonic() > deadline:
                process.kill()
                pytest.fail(f"HTTP server didn't start: {process.stderr.read()}")
            time.sleep(0.05)
    yield f"http://127.0.0.1:{port}", received_keys
    process.terminate()
    process.wait(timeout=5)


def post(url: str, headers: dict[str, str]) -> tuple[int, str]:
    request = urllib.request.Request(
        url,
        data=json.dumps(INITIALIZE).encode(),
        headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream", **headers},
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode()


@pytest.mark.parametrize(
    ("headers", "reason"),
    [
        ({}, "No Authorization header was sent"),
        # What Claude Code sends when the token's environment variable isn't set (checked by capturing it)
        ({"Authorization": "Bearer ${AWARD_SEARCH_MCP_TOKEN}"}, "AWARD_SEARCH_MCP_TOKEN wasn't set"),
        ({"Authorization": "Bearer ${OTHER_NAME:-}"}, "OTHER_NAME wasn't set"),
        ({"Authorization": "Bearer "}, "no token after"),
        ({"Authorization": "Bearer"}, "no token after"),
        ({"Authorization": TOKEN}, "must start with"),
        ({"Authorization": "Bearer wrong-token-that-is-also-long-0123456789"}, "doesn't match"),
        ({"Authorization": f"Bearer {TOKEN[:-1]}"}, "doesn't match"),  # all but the last character
    ],
    ids=["no header", "unset variable", "unset variable with default", "empty token", "empty token, trimmed", "no Bearer prefix", "wrong token", "almost right"],
)
def test_rejections_say_exactly_what_was_wrong(http_server, headers, reason):
    base_url, received_keys = http_server

    status, body = post(f"{base_url}/mcp", headers)

    assert status == 401
    answer = json.loads(body)
    assert answer["error"] == "unauthorized"
    assert reason in answer["reason"]
    # Never echo the expected token or what was sent
    assert TOKEN not in body
    assert TOKEN[:-1] not in body
    assert "wrong-token" not in body
    assert received_keys == []  # nothing reached Seats.aero


def test_every_path_needs_the_token(http_server):
    base_url, _ = http_server
    assert post(f"{base_url}/", {})[0] == 401
    assert post(f"{base_url}/anything", {})[0] == 401


@pytest.mark.anyio
async def test_client_with_the_token_can_search(http_server):
    base_url, received_keys = http_server
    http_client = create_mcp_http_client(headers={"Authorization": f"Bearer {TOKEN}"})

    async with Client(streamable_http_client(f"{base_url}/mcp", http_client=http_client)) as client:
        tools = await client.list_tools()
        assert [tool.name for tool in tools.tools] == ["search_award_seats"]
        result = await client.call_tool(
            "search_award_seats",
            {
                "origins": ["LHR"],
                "destinations": ["LAX"],
                "start_date": "2027-07-17",
                "end_date": "2027-07-17",
                "cabin": "premium",
                "seats": 2,
                "points_limit": 75000,
            },
        )

    assert not result.is_error
    assert result.structured_content["status"] == "found"
    assert result.structured_content["total_found"] == 2
    # The Seats.aero key reached Seats.aero, but neither secret reached the agent
    assert received_keys == ["fake-key"]
    assert "fake-key" not in str(result)
    assert TOKEN not in str(result)


@pytest.mark.parametrize("token", ["", "too-short"], ids=["missing", "too short"])
def test_server_refuses_to_start_without_a_strong_token(token):
    process = start_http_server({"MCP_BEARER_TOKEN": token, "PORT": str(free_port())})
    _, stderr = process.communicate(timeout=10)

    assert process.returncode != 0
    assert "MCP_BEARER_TOKEN must be at least 32 characters" in stderr


def test_secrets_are_loaded_from_parameter_store(monkeypatch):
    from award_search.http_app import load_secrets_from_parameter_store

    class FakeParameterStore:
        requests = []

        def get_parameter(self, Name, WithDecryption):
            self.requests.append((Name, WithDecryption))
            return {"Parameter": {"Value": f"value of {Name}"}}

    for name in ("SEATS_AERO_API_KEY", "MCP_BEARER_TOKEN"):
        monkeypatch.delenv(name, raising=False)  # also restores the environment after the test

    load_secrets_from_parameter_store("/award-search/", ssm=FakeParameterStore())

    assert FakeParameterStore.requests == [
        ("/award-search/SEATS_AERO_API_KEY", True),
        ("/award-search/MCP_BEARER_TOKEN", True),
    ]
    assert os.environ["SEATS_AERO_API_KEY"] == "value of /award-search/SEATS_AERO_API_KEY"
    assert os.environ["MCP_BEARER_TOKEN"] == "value of /award-search/MCP_BEARER_TOKEN"
