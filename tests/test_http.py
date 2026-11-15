"""HTTP transport: bearer token and exposure checks (no network, fake token)."""

from __future__ import annotations

import httpx
import pytest

from media_mcp.config import load_config
from media_mcp.http import build_http_app, check_exposure
from media_mcp.server import build_server

TOKEN = "test-token-not-real"
INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
    "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}}
HDRS = {"Accept": "application/json, text/event-stream"}


def test_refuses_lan_bind_without_token():
    with pytest.raises(SystemExit):
        check_exposure(load_config(env={"MEDIA_MCP_HOST": "0.0.0.0"}))
    check_exposure(load_config(env={"MEDIA_MCP_HOST": "0.0.0.0", "MEDIA_MCP_BEARER_TOKEN": TOKEN}))
    check_exposure(load_config(env={}))


async def test_bearer_token_required_and_lan_host_accepted():
    config = load_config(env={"SONARR_API_KEY": "x", "MEDIA_MCP_HOST": "0.0.0.0", "MEDIA_MCP_BEARER_TOKEN": TOKEN})
    app = build_http_app(build_server(config), config)
    inner = app.app  # the Starlette app, for its lifespan
    async with inner.router.lifespan_context(inner):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://192.0.2.10:8765") as c:
            assert (await c.post("/mcp", json=INIT, headers=HDRS)).status_code == 401
            bad = {**HDRS, "Authorization": "Bearer wrong"}
            assert (await c.post("/mcp", json=INIT, headers=bad)).status_code == 401
            good = {**HDRS, "Authorization": f"Bearer {TOKEN}"}
            r = await c.post("/mcp", json=INIT, headers=good)
            assert r.status_code == 200 and "serverInfo" in r.text


async def test_loopback_without_token_rejects_foreign_host():
    config = load_config(env={"SONARR_API_KEY": "x"})
    app = build_http_app(build_server(config), config)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8765") as c:
            assert (await c.post("/mcp", json=INIT, headers=HDRS)).status_code == 200
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://evil.test") as c:
            assert (await c.post("/mcp", json=INIT, headers=HDRS)).status_code != 200
