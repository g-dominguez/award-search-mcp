"""
The same search_award_seats server, served over HTTP for hosting, with a bearer token check.

Run it locally with: python -m award_search.http_app
It needs MCP_BEARER_TOKEN (what clients must send) and SEATS_AERO_API_KEY in the environment.
On Lambda, both are loaded from Parameter Store instead (see template.yaml).
Every request must send "Authorization: Bearer <token>"; anything else gets a 401 before
the MCP server sees it. Neither secret appears in responses or logs.
"""

import hmac
import json
import os
import sys

import uvicorn
from starlette.types import ASGIApp, Receive, Scope, Send

from award_search.server import mcp

# Long enough that guessing it is hopeless, e.g. from: python -c "import secrets; print(secrets.token_urlsafe(32))"
MIN_TOKEN_LENGTH = 32


class RequireBearerToken:
    """Rejects any HTTP request that doesn't carry the expected bearer token.

    Checked here rather than with the SDK's auth settings, because those make the server
    announce OAuth, and Claude Code then ignores a static Authorization header.
    """

    def __init__(self, app: ASGIApp, token: str):
        self.app = app
        self.expected = f"Bearer {token}".encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # "lifespan" messages are the server starting and stopping, not requests
        if scope["type"] == "http" and not self._authorized(scope):
            await self._reject(send)
            return
        await self.app(scope, receive, send)

    def _authorized(self, scope: Scope) -> bool:
        sent = dict(scope["headers"]).get(b"authorization", b"")
        # compare_digest takes the same time however many characters match, so timing can't reveal the token
        return hmac.compare_digest(sent, self.expected)

    @staticmethod
    async def _reject(send: Send) -> None:
        body = json.dumps({"error": "unauthorized"}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"www-authenticate", b"Bearer"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


def create_app(token: str) -> ASGIApp:
    """The MCP server as a web app at /mcp, behind the token check.

    Stateless with plain JSON responses: each request stands alone, so any running copy of
    the server can answer it. That's what lets it run on Lambda.
    """
    if len(token) < MIN_TOKEN_LENGTH:
        raise ValueError(f"MCP_BEARER_TOKEN must be at least {MIN_TOKEN_LENGTH} characters.")
    # host="0.0.0.0" turns off the SDK's localhost-only Host header check, which would reject
    # requests to the Lambda's own address. The token check protects the server instead.
    app = mcp.streamable_http_app(stateless_http=True, json_response=True, host="0.0.0.0")
    return RequireBearerToken(app, token)


SECRET_NAMES = ("SEATS_AERO_API_KEY", "MCP_BEARER_TOKEN")


def load_secrets_from_parameter_store(prefix: str, ssm=None) -> None:
    """Copy the secrets from Parameter Store into the environment, where the rest of the code reads them.

    Used on Lambda, where the deployment template can't hand encrypted parameters to the function.
    Locally the secrets come straight from environment variables and this isn't called.
    """
    if ssm is None:
        import boto3  # included in Lambda's Python runtime, so it isn't in requirements.txt

        ssm = boto3.client("ssm")
    for name in SECRET_NAMES:
        response = ssm.get_parameter(Name=f"{prefix}{name}", WithDecryption=True)
        os.environ[name] = response["Parameter"]["Value"]


def main() -> None:
    # Set on Lambda by template.yaml, e.g. "/award-search/"
    prefix = os.getenv("PARAMETER_STORE_PREFIX")
    if prefix:
        load_secrets_from_parameter_store(prefix)
    token = os.getenv("MCP_BEARER_TOKEN", "").strip()
    try:
        app = create_app(token)
    except ValueError as error:
        sys.exit(str(error))
    # Lambda Web Adapter sends requests to port 8080 by default
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8080"))
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
