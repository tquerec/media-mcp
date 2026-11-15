"""Builds the MCP server, registering a tool group for each configured service."""

from __future__ import annotations

import sys
from typing import Any

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


def build_server(config: Config, transport: httpx.AsyncBaseTransport | None = None) -> MCPServer:
    """Create the server. ``transport`` lets tests inject a mock HTTP transport."""
    server = MCPServer(name="media-mcp", version=__version__, instructions=INSTRUCTIONS)

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
