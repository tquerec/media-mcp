"""Single-user OAuth for exposing media-mcp beyond localhost.

Claude's custom connectors (desktop, web and mobile) speak MCP's OAuth flow: they
register a client, send the user to /authorize, and exchange the code for tokens.
This module is a small OAuth authorization server whose only "identity provider"
is a password you set in MEDIA_MCP_PASSWORD_HASH (or MEDIA_MCP_PASSWORD).

Security properties:
- Tokens are random 256-bit values and only their SHA-256 hashes are stored.
- PKCE and code expiry are enforced by the MCP SDK's token endpoint.
- Redirect URIs must be on an allow-listed host (Claude's by default), so a
  third-party site can't register a client and phish a login.
- Failed logins are slowed down and lock the login form for a while after
  repeated failures.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import html
import json
import os
import secrets
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from urllib.parse import urlparse

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    RegistrationError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

ACCESS_TOKEN_TTL = 60 * 60            # 1 hour
REFRESH_TOKEN_TTL = 60 * 60 * 24 * 30  # 30 days, rotated on every use
AUTH_CODE_TTL = 5 * 60
PENDING_TTL = 10 * 60
MAX_FAILURES = 5
LOCKOUT_SECONDS = 15 * 60

DEFAULT_REDIRECT_HOSTS = ("claude.ai", "claude.com", "localhost", "127.0.0.1")

SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 1


# --- password hashing -----------------------------------------------------

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32)
    b64 = lambda b: base64.urlsafe_b64encode(b).decode().rstrip("=")  # noqa: E731
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${b64(salt)}${b64(digest)}"


def verify_password(password: str, *, stored_hash: str | None, plain: str | None) -> bool:
    if stored_hash:
        try:
            _, n, r, p, salt, digest = stored_hash.split("$")
            pad = lambda s: base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))  # noqa: E731
            expected = pad(digest)
            actual = hashlib.scrypt(password.encode(), salt=pad(salt), n=int(n), r=int(r), p=int(p),
                                    dklen=len(expected))
            return hmac.compare_digest(actual, expected)
        except (ValueError, TypeError):
            return False
    if plain:
        return hmac.compare_digest(password.encode(), plain.encode())
    return False


def _h(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# --- provider -------------------------------------------------------------

@dataclass
class _Pending:
    client_id: str
    client_name: str | None
    params: AuthorizationParams
    expires_at: float


@dataclass
class PasswordOAuthProvider:
    """OAuthAuthorizationServerProvider backed by one password and a JSON state file."""

    public_url: str
    password_hash: str | None = None
    password_plain: str | None = None
    state_file: Path | None = None
    redirect_hosts: tuple[str, ...] = DEFAULT_REDIRECT_HOSTS

    _clients: dict[str, OAuthClientInformationFull] = field(default_factory=dict)
    _access: dict[str, AccessToken] = field(default_factory=dict)    # keyed by token hash
    _refresh: dict[str, RefreshToken] = field(default_factory=dict)  # keyed by token hash
    _codes: dict[str, AuthorizationCode] = field(default_factory=dict)
    _pending: dict[str, _Pending] = field(default_factory=dict)
    _failures: list[float] = field(default_factory=list)
    _locked_until: float = 0.0

    def __post_init__(self) -> None:
        self.public_url = self.public_url.rstrip("/")
        if not (self.password_hash or self.password_plain):
            raise ValueError("Remote access needs MEDIA_MCP_PASSWORD_HASH or MEDIA_MCP_PASSWORD")
        self._load()

    # persistence: clients and token hashes survive restarts
    def _load(self) -> None:
        if not self.state_file or not self.state_file.exists():
            return
        try:
            data = json.loads(self.state_file.read_text())
        except (OSError, ValueError):
            print("media-mcp: could not read auth state; starting fresh", file=sys.stderr)
            return
        now = time.time()
        self._clients = {k: OAuthClientInformationFull.model_validate(v) for k, v in data.get("clients", {}).items()}
        self._access = {k: AccessToken.model_validate(v) for k, v in data.get("access", {}).items()
                        if (v.get("expires_at") or now + 1) > now}
        self._refresh = {k: RefreshToken.model_validate(v) for k, v in data.get("refresh", {}).items()
                         if (v.get("expires_at") or now + 1) > now}

    def _save(self) -> None:
        if not self.state_file:
            return
        data = {
            "clients": {k: v.model_dump(mode="json") for k, v in self._clients.items()},
            "access": {k: v.model_dump(mode="json") for k, v in self._access.items()},
            "refresh": {k: v.model_dump(mode="json") for k, v in self._refresh.items()},
        }
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data))
        os.replace(tmp, self.state_file)

    # clients
    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self._clients.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        for uri in client_info.redirect_uris or []:
            host = urlparse(str(uri)).hostname or ""
            if not any(host == h or host.endswith("." + h) for h in self.redirect_hosts):
                raise RegistrationError("invalid_redirect_uri", f"redirect host {host!r} is not allowed")
        self._clients[client_info.client_id] = client_info
        self._save()

    # authorize: park the request and send the browser to our login page
    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        if params.resource and params.resource.rstrip("/") not in (self.public_url, self.public_url + "/mcp"):
            raise AuthorizeError("invalid_target", "unknown resource")
        self._gc()
        request_id = secrets.token_urlsafe(32)
        self._pending[request_id] = _Pending(client.client_id, client.client_name, params, time.time() + PENDING_TTL)
        return f"{self.public_url}/login?request={request_id}"

    async def load_authorization_code(self, client: OAuthClientInformationFull, authorization_code: str):
        code = self._codes.get(authorization_code)
        return code if code and code.client_id == client.client_id else None

    async def exchange_authorization_code(self, client, authorization_code: AuthorizationCode) -> OAuthToken:
        self._codes.pop(authorization_code.code, None)
        return self._issue(client.client_id, authorization_code.scopes, authorization_code.resource)

    async def load_refresh_token(self, client, refresh_token: str) -> RefreshToken | None:
        rt = self._refresh.get(_h(refresh_token))
        return rt if rt and rt.client_id == client.client_id else None

    async def exchange_refresh_token(self, client, refresh_token: RefreshToken, scopes: list[str]) -> OAuthToken:
        self._refresh.pop(refresh_token.token, None)  # rotate
        return self._issue(client.client_id, scopes or refresh_token.scopes, refresh_token.resource)

    async def load_access_token(self, token: str) -> AccessToken | None:
        at = self._access.get(_h(token))
        if at and at.expires_at and at.expires_at < time.time():
            self._access.pop(at.token, None)
            return None
        return at

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        # Revoke everything for that client: simplest correct option for a single user.
        cid = token.client_id
        self._access = {k: v for k, v in self._access.items() if v.client_id != cid}
        self._refresh = {k: v for k, v in self._refresh.items() if v.client_id != cid}
        self._save()

    def _issue(self, client_id: str, scopes: list[str], resource: str | None) -> OAuthToken:
        now = int(time.time())
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        self._access[_h(access)] = AccessToken(token=_h(access), client_id=client_id, scopes=scopes,
                                               expires_at=now + ACCESS_TOKEN_TTL, resource=resource,
                                               subject="owner")
        self._refresh[_h(refresh)] = RefreshToken(token=_h(refresh), client_id=client_id, scopes=scopes,
                                                  expires_at=now + REFRESH_TOKEN_TTL, resource=resource,
                                                  subject="owner")
        self._gc()
        self._save()
        return OAuthToken(access_token=access, token_type="Bearer", expires_in=ACCESS_TOKEN_TTL,
                          refresh_token=refresh, scope=" ".join(scopes) or None)

    def _gc(self) -> None:
        now = time.time()
        self._pending = {k: v for k, v in self._pending.items() if v.expires_at > now}
        self._codes = {k: v for k, v in self._codes.items() if v.expires_at > now}
        self._access = {k: v for k, v in self._access.items() if not v.expires_at or v.expires_at > now}
        self._refresh = {k: v for k, v in self._refresh.items() if not v.expires_at or v.expires_at > now}

    # --- login page -------------------------------------------------------

    async def login_page(self, request: Request) -> Response:
        if request.method == "GET":
            return self._render(request.query_params.get("request", ""))

        form = await request.form()
        request_id = str(form.get("request", ""))
        password = str(form.get("password", ""))
        pending = self._pending.get(request_id)
        if not pending or pending.expires_at < time.time():
            return self._render("", error="This sign-in link expired. Start again from Claude.", status=400)

        now = time.time()
        if now < self._locked_until:
            return self._render(request_id, error="Too many failed attempts. Try again later.", status=429)

        if not verify_password(password, stored_hash=self.password_hash, plain=self.password_plain):
            self._failures = [t for t in self._failures if t > now - LOCKOUT_SECONDS] + [now]
            if len(self._failures) >= MAX_FAILURES:
                self._locked_until = now + LOCKOUT_SECONDS
                self._failures.clear()
                print("media-mcp: login locked after repeated failures", file=sys.stderr)
            await asyncio.sleep(1.0)
            return self._render(request_id, error="Wrong password.", status=401)

        self._failures.clear()
        del self._pending[request_id]
        p = pending.params
        code = secrets.token_urlsafe(32)
        self._codes[code] = AuthorizationCode(
            code=code, scopes=p.scopes or [], expires_at=time.time() + AUTH_CODE_TTL,
            client_id=pending.client_id, code_challenge=p.code_challenge, redirect_uri=p.redirect_uri,
            redirect_uri_provided_explicitly=p.redirect_uri_provided_explicitly, resource=p.resource,
            subject="owner",
        )
        return RedirectResponse(construct_redirect_uri(str(p.redirect_uri), code=code, state=p.state),
                                status_code=302, headers=_SECURITY_HEADERS)

    def _render(self, request_id: str, error: str | None = None, status: int = 200) -> HTMLResponse:
        pending = self._pending.get(request_id)
        if pending is None and not error:
            error, status = "This sign-in link is invalid or expired. Start again from Claude.", 400
        who = html.escape(pending.client_name or "An MCP client") if pending else ""
        dest = html.escape(urlparse(str(pending.params.redirect_uri)).hostname or "") if pending else ""
        form = "" if pending is None else f"""
          <p><b>{who}</b> wants access to your media server. After you sign in you'll be sent back to
          <b>{dest}</b>.</p>
          <form method="post" action="/login">
            <input type="hidden" name="request" value="{html.escape(request_id)}">
            <label>Password <input type="password" name="password" autofocus required autocomplete="current-password"></label>
            <button type="submit">Allow</button>
          </form>"""
        err = f'<p class="err">{html.escape(error)}</p>' if error else ""
        body = f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>media-mcp sign in</title>
<style>body{{font:16px system-ui,sans-serif;max-width:26rem;margin:3rem auto;padding:0 1rem;color:#222}}
input{{display:block;width:100%;padding:.6rem;margin:.4rem 0 1rem;font-size:1rem;box-sizing:border-box}}
button{{padding:.6rem 1.2rem;font-size:1rem}}.err{{color:#b00020}}
@media (prefers-color-scheme:dark){{body{{background:#111;color:#eee}}}}</style></head>
<body><h1>media-mcp</h1>{err}{form}</body></html>"""
        return HTMLResponse(body, status_code=status, headers=_SECURITY_HEADERS)


_SECURITY_HEADERS = {
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self' https: http://localhost:* http://127.0.0.1:*; frame-ancestors 'none'",
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
}


def provider_from_env(env: dict[str, str], public_url: str) -> PasswordOAuthProvider:
    hosts = tuple(h.strip() for h in (env.get("MEDIA_MCP_ALLOWED_REDIRECT_HOSTS") or "").split(",") if h.strip())
    state = env.get("MEDIA_MCP_STATE_FILE") or ".media-mcp-auth.json"
    return PasswordOAuthProvider(
        public_url=public_url,
        password_hash=(env.get("MEDIA_MCP_PASSWORD_HASH") or "").strip() or None,
        password_plain=env.get("MEDIA_MCP_PASSWORD") or None,
        state_file=Path(state),
        redirect_hosts=hosts or DEFAULT_REDIRECT_HOSTS,
    )


