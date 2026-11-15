"""End-to-end OAuth flow against the ASGI app, with no network and fake secrets."""

from __future__ import annotations

import base64
import hashlib
import secrets
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from media_mcp.auth import hash_password, verify_password
from media_mcp.config import load_config
from media_mcp.server import build_server, check_exposure, transport_security

PUBLIC = "https://media.example.test"
PASSWORD = "correct horse battery staple"
REDIRECT = "https://claude.ai/api/mcp/auth_callback"


@pytest.fixture
def app(tmp_path):
    env = {
        "SONARR_API_KEY": "fake",
        "MEDIA_MCP_TRANSPORT": "streamable-http",
        "MEDIA_MCP_PUBLIC_URL": PUBLIC,
        "MEDIA_MCP_PASSWORD_HASH": hash_password(PASSWORD),
        "MEDIA_MCP_STATE_FILE": str(tmp_path / "state.json"),
    }
    config = load_config(env=env)
    server = build_server(config)
    return server.streamable_http_app(transport_security=transport_security(config))


def test_password_hash_roundtrip():
    h = hash_password("hunter2hunter2")
    assert verify_password("hunter2hunter2", stored_hash=h, plain=None)
    assert not verify_password("wrong", stored_hash=h, plain=None)
    assert not verify_password("x", stored_hash=None, plain=None)


def test_refuses_public_bind_without_auth():
    with pytest.raises(SystemExit):
        check_exposure(load_config(env={"MEDIA_MCP_TRANSPORT": "streamable-http", "MEDIA_MCP_HOST": "0.0.0.0"}))
    check_exposure(load_config(env={"MEDIA_MCP_TRANSPORT": "streamable-http"}))  # loopback is fine


async def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=PUBLIC)


async def test_full_oauth_flow(app):
    async with app.router.lifespan_context(app), await _client(app) as c:
        # Unauthenticated MCP request is rejected and points at resource metadata.
        r = await c.post("/mcp", json={})
        assert r.status_code == 401 and "resource_metadata" in r.headers.get("www-authenticate", "")

        # Dynamic client registration: a non-allowlisted redirect host is refused.
        bad = await c.post("/register", json={"redirect_uris": ["https://evil.example/cb"], "client_name": "x",
                                              "token_endpoint_auth_method": "none"})
        assert bad.status_code == 400
        reg = (await c.post("/register", json={"redirect_uris": [REDIRECT], "client_name": "Claude",
                                               "token_endpoint_auth_method": "none"})).json()

        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        r = await c.get("/authorize", params={
            "response_type": "code", "client_id": reg["client_id"], "redirect_uri": REDIRECT,
            "code_challenge": challenge, "code_challenge_method": "S256", "state": "s1",
            "resource": f"{PUBLIC}/mcp"})
        assert r.status_code == 302
        login = urlparse(r.headers["location"])
        request_id = parse_qs(login.query)["request"][0]

        page = await c.get(f"/login?request={request_id}")
        assert page.status_code == 200 and "Claude" in page.text and page.headers["x-frame-options"] == "DENY"

        wrong = await c.post("/login", data={"request": request_id, "password": "nope"})
        assert wrong.status_code == 401

        ok = await c.post("/login", data={"request": request_id, "password": PASSWORD})
        assert ok.status_code == 302
        back = urlparse(ok.headers["location"])
        assert f"{back.scheme}://{back.netloc}{back.path}" == REDIRECT
        q = parse_qs(back.query)
        assert q["state"] == ["s1"]

        tok = (await c.post("/token", data={
            "grant_type": "authorization_code", "code": q["code"][0], "redirect_uri": REDIRECT,
            "client_id": reg["client_id"], "code_verifier": verifier, "resource": f"{PUBLIC}/mcp"})).json()
        assert tok["token_type"].lower() == "bearer" and tok["refresh_token"]

        # The code is single-use.
        again = await c.post("/token", data={
            "grant_type": "authorization_code", "code": q["code"][0], "redirect_uri": REDIRECT,
            "client_id": reg["client_id"], "code_verifier": verifier})
        assert again.status_code == 400

        r = await c.post("/mcp", headers={"Authorization": f"Bearer {tok['access_token']}",
                                          "Accept": "application/json, text/event-stream"},
                         json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                             "protocolVersion": "2025-06-18", "capabilities": {},
                             "clientInfo": {"name": "t", "version": "1"}}})
        assert r.status_code == 200 and "serverInfo" in r.text

        # Refresh rotates: the old refresh token stops working.
        new = (await c.post("/token", data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"],
                                            "client_id": reg["client_id"]})).json()
        assert new["access_token"] != tok["access_token"]
        stale = await c.post("/token", data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"],
                                             "client_id": reg["client_id"]})
        assert stale.status_code == 400


async def test_wrong_host_header_rejected(app):
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://attacker.test") as c:
            r = await c.post("/mcp", json={}, headers={"Authorization": "Bearer x"})
            assert r.status_code in (401, 421, 400, 403)
