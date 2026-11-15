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
import re
import sys

import uvicorn
from starlette.types import ASGIApp, Receive, Scope, Send

from award_search.server import mcp

# Long enough that guessing it is hopeless, e.g. from: python -c "import secrets; print(secrets.token_urlsafe(32))"
MIN_TOKEN_LENGTH = 32

# A client that can't fill in a ${NAME} variable sends the text as is, e.g. Claude Code when NAME isn't set
UNFILLED_VARIABLE = re.compile(rb"^\$\{([A-Za-z_][A-Za-z0-9_]*)(:-[^}]*)?\}$")


class RequireBearerToken:
    """Rejects any HTTP request that doesn't carry the expected bearer token.

    Checked here rather than with the SDK's auth settings, because those make the server
    announce OAuth, and Claude Code then ignores a static Authorization header.
    """

    def __init__(self, app: ASGIApp, token: str):
        self.app = app
        self.expected = token.encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # "lifespan" messages are the server starting and stopping, not requests
        if scope["type"] == "http":
            problem = self._problem(scope)
            if problem:
                await self._reject(send, problem)
                return
        await self.app(scope, receive, send)

    def _problem(self, scope: Scope) -> str | None:
        """What's wrong with the request's token, or None if it's right.

        The reasons are specific so a failure can be fixed without guessing,
        but they never include the expected token or what was sent.
        """
        sent = dict(scope["headers"]).get(b"authorization")
        if sent is None:
            return 'No Authorization header was sent. Send "Authorization: Bearer <token>".'
        scheme, _, token = sent.strip().partition(b" ")
        if scheme.lower() != b"bearer":
            return 'The Authorization header must start with "Bearer " followed by the token.'
        token = token.strip()
        if not token:
            return 'The Authorization header had no token after "Bearer", so the client\'s token setting is empty.'
        unfilled = UNFILLED_VARIABLE.match(token)
        if unfilled:
            name = unfilled.group(1).decode()
            return (
                f"The client sent the text ${{{name}}} instead of a token, because the environment variable "
                f"{name} wasn't set when the client started. Set it in the shell that starts the client "
                "(for Claude Code, open a new terminal or run: source ~/.zshrc), then restart the client."
            )
        # compare_digest takes the same time however many characters match, so timing can't reveal the token
        if not hmac.compare_digest(token, self.expected):
            return "The bearer token doesn't match the token this server was started with."
        return None

    @staticmethod
    async def _reject(send: Send, reason: str) -> None:
        body = json.dumps({"error": "unauthorized", "reason": reason}).encode()
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
