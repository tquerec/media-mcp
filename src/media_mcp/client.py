"""Small async HTTP clients for the *arr apps and SABnzbd."""

from __future__ import annotations

from typing import Any

import httpx
from mcp.server.mcpserver.exceptions import ToolError

from .config import ServiceConfig


class ServiceError(ToolError):
    """A service returned an error or could not be reached. The message is shown to the model."""


def _describe(exc: Exception, service: str) -> ServiceError:
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 401:
            return ServiceError(f"{service}: unauthorized (check the API key)")
        body = exc.response.text[:300]
        return ServiceError(f"{service}: HTTP {status}: {body}")
    if isinstance(exc, httpx.RequestError):
        return ServiceError(f"{service}: could not connect ({type(exc).__name__})")
    return ServiceError(f"{service}: {exc}")


class ArrClient:
    """Client for Sonarr/Radarr (API v3) and Lidarr (API v1)."""

    def __init__(self, cfg: ServiceConfig, api_version: str, timeout: float = 30.0,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.name = cfg.name
        self._client = httpx.AsyncClient(
            base_url=f"{cfg.url}/api/{api_version}",
            headers={"X-Api-Key": cfg.api_key, "Accept": "application/json"},
            timeout=timeout,
            transport=transport,
        )

    async def request(self, method: str, path: str, *, params: dict[str, Any] | None = None,
                      json: Any = None) -> Any:
        try:
            resp = await self._client.request(method, path, params=params, json=json)
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            raise _describe(exc, self.name) from None
        if not resp.content:
            return None
        return resp.json()

    async def get(self, path: str, **params: Any) -> Any:
        clean = {k: v for k, v in params.items() if v is not None}
        return await self.request("GET", path, params=clean)

    async def post(self, path: str, json: Any) -> Any:
        return await self.request("POST", path, json=json)

    async def aclose(self) -> None:
        await self._client.aclose()


class SabClient:
    """Client for the SABnzbd API (``/api?mode=...``)."""

    def __init__(self, cfg: ServiceConfig, timeout: float = 30.0,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.name = cfg.name
        self._api_key = cfg.api_key
        self._client = httpx.AsyncClient(base_url=cfg.url, timeout=timeout, transport=transport)

    async def call(self, mode: str, **params: Any) -> Any:
        query = {"mode": mode, "output": "json", "apikey": self._api_key}
        query.update({k: v for k, v in params.items() if v is not None})
        try:
            resp = await self._client.get("/api", params=query)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            raise _describe(exc, self.name) from None
        if isinstance(data, dict) and data.get("status") is False:
            raise ServiceError(f"{self.name}: {data.get('error', 'request failed')}")
        return data

    async def aclose(self) -> None:
        await self._client.aclose()
