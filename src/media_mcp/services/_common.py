"""Helpers shared by the Sonarr, Radarr and Lidarr tool groups."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from ..client import ArrClient

READ = ToolAnnotations(read_only_hint=True, open_world_hint=False)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)


def gib(size: int | float | None) -> float | None:
    return None if size is None else round(size / 1024**3, 2)


def pick(obj: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {k: obj.get(k) for k in keys if obj.get(k) is not None}


def summarize_queue_item(rec: dict[str, Any]) -> dict[str, Any]:
    size = rec.get("size") or 0
    left = rec.get("sizeleft") or 0
    out = pick(rec, "id", "title", "status", "trackedDownloadState", "trackedDownloadStatus",
               "timeleft", "downloadClient", "protocol", "errorMessage")
    out["progress_pct"] = round(100 * (size - left) / size, 1) if size else None
    out["size_gb"] = gib(size)
    msgs = [m for s in rec.get("statusMessages") or [] for m in s.get("messages") or []]
    if msgs:
        out["messages"] = msgs[:5]
    return out


def date_window(days_back: int, days_ahead: int) -> tuple[str, str]:
    today = date.today()
    return ((today - timedelta(days=days_back)).isoformat(),
            (today + timedelta(days=days_ahead)).isoformat())


async def resolve_defaults(client: ArrClient, quality_profile_id: int | None,
                           root_folder_path: str | None) -> tuple[int, str]:
    """Fall back to the first quality profile / root folder when not specified."""
    if quality_profile_id is None:
        profiles = await client.get("/qualityprofile")
        if not profiles:
            raise ToolError("No quality profiles configured")
        quality_profile_id = profiles[0]["id"]
    if root_folder_path is None:
        folders = await client.get("/rootfolder")
        if not folders:
            raise ToolError("No root folders configured")
        root_folder_path = folders[0]["path"]
    return quality_profile_id, root_folder_path


def register_common(server: MCPServer, client: ArrClient, prefix: str, queue_params: dict[str, Any]) -> None:
    """Tools every *arr app supports the same way."""
    label = prefix.capitalize()

    @server.tool(name=f"{prefix}_system_status", annotations=READ,
                 description=f"{label} version, uptime info and any health warnings.")
    async def system_status() -> dict[str, Any]:
        status = await client.get("/system/status")
        health = await client.get("/health")
        return {
            "status": pick(status, "appName", "version", "startTime", "osName", "branch"),
            "health": [pick(h, "type", "source", "message") for h in health or []],
        }

    @server.tool(name=f"{prefix}_queue", annotations=READ,
                 description=f"Items {label} is currently downloading or importing.")
    async def queue(page_size: int = 50) -> dict[str, Any]:
        data = await client.get("/queue", pageSize=page_size, **queue_params)
        records = data.get("records", []) if isinstance(data, dict) else data or []
        return {"total": data.get("totalRecords", len(records)) if isinstance(data, dict) else len(records),
                "items": [summarize_queue_item(r) for r in records]}

    @server.tool(name=f"{prefix}_quality_profiles", annotations=READ,
                 description=f"List {label} quality profiles (ids are used when adding media).")
    async def quality_profiles() -> list[dict[str, Any]]:
        return [pick(p, "id", "name") for p in await client.get("/qualityprofile")]

    @server.tool(name=f"{prefix}_root_folders", annotations=READ,
                 description=f"List {label} root folders with free space.")
    async def root_folders() -> list[dict[str, Any]]:
        return [{"path": f.get("path"), "free_gb": gib(f.get("freeSpace")), "accessible": f.get("accessible")}
                for f in await client.get("/rootfolder")]
