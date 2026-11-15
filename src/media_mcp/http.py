"""Streamable-HTTP serving, with an optional shared bearer token for LAN access."""

from __future__ import annotations

import hmac
import sys

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.types import ASGIApp, Receive, Scope, Send

from .config import Config

LOOPBACK = {"127.0.0.1", "localhost", "::1"}


class BearerTokenMiddleware:
    """Reject any HTTP request that doesn't carry ``Authorization: Bearer <token>``."""

    def __init__(self, app: ASGIApp, token: str):
        self.app = app
        self._expected = f"Bearer {token}".encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            supplied = dict(scope.get("headers") or []).get(b"authorization", b"")
            if not hmac.compare_digest(supplied, self._expected):
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"text/plain"),
                                        (b"www-authenticate", b'Bearer realm="media-mcp"')]})
                await send({"type": "http.response.body", "body": b"unauthorized"})
                return
        await self.app(scope, receive, send)


def check_exposure(config: Config) -> None:
    """Refuse to listen beyond loopback without a token, unless explicitly allowed."""
    if config.host in LOOPBACK or config.bearer_token or config.allow_unauthenticated:
        return
    raise SystemExit(
        f"media-mcp: refusing to listen on {config.host} without MEDIA_MCP_BEARER_TOKEN. "
        "Generate one with `media-mcp new-token`, or set MEDIA_MCP_ALLOW_UNAUTHENTICATED=true "
        "if something else protects the port."
    )


def build_http_app(server: MCPServer, config: Config) -> ASGIApp:
    if config.bearer_token:
        # Every request needs the token, so a malicious web page can't use the server via DNS
        # rebinding; Host checks would only get in the way of reaching it by LAN IP or name.
        security = TransportSecuritySettings(enable_dns_rebinding_protection=False)
    else:
        security = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*"],
            allowed_origins=["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"],
        )
    app: ASGIApp = server.streamable_http_app(transport_security=security, host=config.host)
    if config.bearer_token:
        app = BearerTokenMiddleware(app, config.bearer_token)
    return app


def serve_http(server: MCPServer, config: Config) -> None:
    import uvicorn

    check_exposure(config)
    auth = "bearer token required" if config.bearer_token else "no auth (loopback only)"
    print(f"media-mcp: listening on http://{config.host}:{config.port}/mcp ({auth})", file=sys.stderr)
    uvicorn.run(build_http_app(server, config), host=config.host, port=config.port, log_level="warning")
