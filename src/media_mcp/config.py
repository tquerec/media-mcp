"""Configuration, read from environment variables (optionally via a local .env file).

Nothing here has real credentials: every secret comes from the environment.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_URLS = {
    "sonarr": "http://localhost:8989",
    "radarr": "http://localhost:7878",
    "lidarr": "http://localhost:8686",
    "sabnzbd": "http://localhost:8080",
}

SERVICES = tuple(DEFAULT_URLS)


@dataclass(frozen=True)
class ServiceConfig:
    name: str
    url: str
    api_key: str

    def __repr__(self) -> str:  # never print the key
        return f"ServiceConfig(name={self.name!r}, url={self.url!r}, api_key='***')"


@dataclass(frozen=True)
class Config:
    services: dict[str, ServiceConfig] = field(default_factory=dict)
    read_only: bool = False
    timeout: float = 30.0
    transport: str = "stdio"
    host: str = "127.0.0.1"
    port: int = 8765

    def enabled(self, name: str) -> bool:
        return name in self.services


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def load_config(env: dict[str, str] | None = None, env_file: str | Path | None = None) -> Config:
    """Build a Config from the environment.

    If ``env`` is None, the process environment is used, after loading a .env file
    (``MEDIA_MCP_ENV_FILE`` or ``./.env``) without overriding variables already set.
    """
    if env is None:
        path = env_file or os.environ.get("MEDIA_MCP_ENV_FILE") or ".env"
        load_dotenv(path, override=False)
        env = dict(os.environ)

    services: dict[str, ServiceConfig] = {}
    for name in SERVICES:
        prefix = name.upper()
        key = (env.get(f"{prefix}_API_KEY") or "").strip()
        if not key:
            continue
        url = (env.get(f"{prefix}_URL") or DEFAULT_URLS[name]).strip().rstrip("/")
        services[name] = ServiceConfig(name=name, url=url, api_key=key)

    return Config(
        services=services,
        read_only=_truthy(env.get("MEDIA_MCP_READ_ONLY")),
        timeout=float(env.get("MEDIA_MCP_TIMEOUT") or 30),
        transport=(env.get("MEDIA_MCP_TRANSPORT") or "stdio").strip(),
        host=(env.get("MEDIA_MCP_HOST") or "127.0.0.1").strip(),
        port=int(env.get("MEDIA_MCP_PORT") or 8765),
    )
