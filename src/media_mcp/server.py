"""Builds the MCP server, registering a tool group for each configured service."""

from __future__ import annotations

import sys
from typing import Any
from urllib.parse import urlparse

import httpx
from mcp.server.mcpserver import MCPServer

from . import __version__
from .client import ArrClient, SabClient
from .config import SERVICES, Config
from .services import lidarr, radarr, sabnzbd, sonarr
from .services._common import READ

INSTRUCTIONS = """\
Tools for a home media stack. Tool names are prefixed by service:
sonarr_* (TV), radarr_* (movies), lidarr_* (music), sabnzbd_* (download client).
Only configured services are available; call media_services to see which.
To add media, look it up first (e.g. sonarr_lookup_series) and pass the returned id to the add tool.
"""

ARR_MODULES = {"sonarr": sonarr, "radarr": radarr, "lidarr": lidarr}


LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def _auth_kwargs(config: Config) -> dict[str, Any]:
    """OAuth settings for remote access, or {} when the server stays local."""
    if not config.public_url:
        return {}
    from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions

    from .auth import provider_from_env

    provider = provider_from_env(config.auth_env, config.public_url)
    return {
        "auth_server_provider": provider,
        "auth": AuthSettings(
            issuer_url=config.public_url,
            resource_server_url=f"{config.public_url}/mcp",
            client_registration_options=ClientRegistrationOptions(enabled=True),
            revocation_options=RevocationOptions(enabled=True),
            # /authorize already rejects foreign resource indicators, and this server only
            # accepts tokens it issued itself, so the extra audience check is not needed.
            validate_token_resource=False,
        ),
    }


def transport_security(config: Config):
    """Host/Origin checks: localhost always, plus the public hostname when one is set."""
    from mcp.server.transport_security import TransportSecuritySettings

    hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*", "127.0.0.1", "localhost"]
    origins = ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]
    if config.public_url:
        parsed = urlparse(config.public_url)
        hosts.append(parsed.netloc)
        origins.append(f"{parsed.scheme}://{parsed.netloc}")
    return TransportSecuritySettings(enable_dns_rebinding_protection=True,
                                     allowed_hosts=hosts, allowed_origins=origins)


def check_exposure(config: Config) -> None:
    """Refuse to serve HTTP beyond loopback without a login, unless explicitly allowed."""
    if config.transport != "streamable-http" or config.public_url or config.allow_unauthenticated:
        return
    if config.host not in LOOPBACK:
        raise SystemExit(
            f"media-mcp: refusing to listen on {config.host} without authentication. Set "
            "MEDIA_MCP_PUBLIC_URL and MEDIA_MCP_PASSWORD_HASH (see README), or "
            "MEDIA_MCP_ALLOW_UNAUTHENTICATED=true if something else protects it."
        )


def build_server(config: Config, transport: httpx.AsyncBaseTransport | None = None) -> MCPServer:
    """Create the server. ``transport`` lets tests inject a mock HTTP transport."""
    auth = _auth_kwargs(config)
    server = MCPServer(name="media-mcp", version=__version__, instructions=INSTRUCTIONS, **auth)
    if auth:
        server.custom_route("/login", methods=["GET", "POST"], include_in_schema=False)(
            auth["auth_server_provider"].login_page)

    for name, module in ARR_MODULES.items():
        if cfg := config.services.get(name):
            client = ArrClient(cfg, module.API_VERSION, config.timeout, transport)
            module.register(server, client, config.read_only)

    if cfg := config.services.get("sabnzbd"):
        sabnzbd.register(server, SabClient(cfg, config.timeout, transport), config.read_only)

    @server.tool(name="media_services", annotations=READ,
                 description="Which media services are configured, and whether write tools are enabled.")
    async def media_services() -> dict[str, Any]:
        return {
            "enabled": [s for s in SERVICES if config.enabled(s)],
            "disabled": [s for s in SERVICES if not config.enabled(s)],
            "read_only": config.read_only,
        }

    if not config.services:
        print("media-mcp: no services configured. Set SONARR_API_KEY, RADARR_API_KEY, "
              "LIDARR_API_KEY and/or SABNZBD_API_KEY (see .env.example).", file=sys.stderr)
    return server
