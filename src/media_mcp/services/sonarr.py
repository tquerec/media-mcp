"""Sonarr (TV) tools."""

from __future__ import annotations

from typing import Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from ..client import ArrClient
from ._common import READ, WRITE, date_window, gib, pick, register_common, resolve_defaults

API_VERSION = "v3"


def _series(s: dict[str, Any]) -> dict[str, Any]:
    stats = s.get("statistics") or {}
    out = pick(s, "id", "title", "year", "tvdbId", "status", "network", "monitored", "path")
    out.update({k: v for k, v in {
        "seasons": stats.get("seasonCount", len(s.get("seasons") or []) or None),
        "episodes_on_disk": stats.get("episodeFileCount"),
        "episodes_total": stats.get("totalEpisodeCount"),
        "size_gb": gib(stats.get("sizeOnDisk")),
    }.items() if v is not None})
    if s.get("overview") and "id" not in s:  # lookup results: include a short overview
        out["overview"] = s["overview"][:300]
    return out


def register(server: MCPServer, client: ArrClient, read_only: bool) -> None:
    register_common(server, client, "sonarr", {"includeSeries": True, "includeEpisode": True})

    @server.tool(name="sonarr_lookup_series", annotations=READ,
                 description="Search for a TV series to add (online lookup). Returns tvdbId for sonarr_add_series.")
    async def lookup_series(term: str, limit: int = 10) -> list[dict[str, Any]]:
        return [_series(s) for s in (await client.get("/series/lookup", term=term))[:limit]]

    @server.tool(name="sonarr_list_series", annotations=READ,
                 description="List series in the Sonarr library, optionally filtered by a title substring.")
    async def list_series(title_contains: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        items = await client.get("/series")
        if title_contains:
            items = [s for s in items if title_contains.lower() in (s.get("title") or "").lower()]
        return [_series(s) for s in items[:limit]]

    @server.tool(name="sonarr_calendar", annotations=READ,
                 description="Episodes airing in a date window around today.")
    async def calendar(days_back: int = 1, days_ahead: int = 7) -> list[dict[str, Any]]:
        start, end = date_window(days_back, days_ahead)
        eps = await client.get("/calendar", start=start, end=end, includeSeries=True)
        return [{
            "series": (e.get("series") or {}).get("title"),
            "season": e.get("seasonNumber"), "episode": e.get("episodeNumber"),
            "title": e.get("title"), "air_date_utc": e.get("airDateUtc"),
            "has_file": e.get("hasFile"), "monitored": e.get("monitored"),
        } for e in eps]

    @server.tool(name="sonarr_missing", annotations=READ,
                 description="Monitored episodes that have aired but are not downloaded.")
    async def missing(page_size: int = 50) -> dict[str, Any]:
        data = await client.get("/wanted/missing", pageSize=page_size, includeSeries=True,
                                sortKey="airDateUtc", sortDirection="descending")
        return {"total": data.get("totalRecords"), "episodes": [{
            "series": (e.get("series") or {}).get("title"),
            "season": e.get("seasonNumber"), "episode": e.get("episodeNumber"),
            "title": e.get("title"), "air_date_utc": e.get("airDateUtc"),
        } for e in data.get("records", [])]}

    if read_only:
        return

    @server.tool(name="sonarr_add_series", annotations=WRITE,
                 description="Add a series to Sonarr by tvdbId (from sonarr_lookup_series). "
                             "Uses the first quality profile/root folder unless given.")
    async def add_series(
        tvdb_id: int,
        quality_profile_id: int | None = None,
        root_folder_path: str | None = None,
        monitor: Literal["all", "future", "missing", "existing", "firstSeason", "lastSeason", "pilot", "none"] = "all",
        search_now: bool = True,
    ) -> dict[str, Any]:
        found = await client.get("/series/lookup", term=f"tvdb:{tvdb_id}")
        if not found:
            raise ToolError(f"No series found for tvdbId {tvdb_id}")
        series = found[0]
        if series.get("id"):
            return {"already_in_library": True, **_series(series)}
        qp, root = await resolve_defaults(client, quality_profile_id, root_folder_path)
        series.update({
            "qualityProfileId": qp, "rootFolderPath": root, "monitored": monitor != "none",
            "seasonFolder": True,
            "addOptions": {"monitor": monitor, "searchForMissingEpisodes": search_now},
        })
        return {"added": True, **_series(await client.post("/series", series))}

    @server.tool(name="sonarr_search_series", annotations=WRITE,
                 description="Trigger Sonarr to search indexers for missing episodes of a library series (by Sonarr id).")
    async def search_series(series_id: int) -> dict[str, Any]:
        cmd = await client.post("/command", {"name": "SeriesSearch", "seriesId": series_id})
        return pick(cmd, "id", "name", "status")
