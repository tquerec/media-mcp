"""Lidarr (music) tools."""

from __future__ import annotations

from typing import Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from ..client import ArrClient
from ._common import READ, WRITE, date_window, gib, pick, register_common, resolve_defaults

API_VERSION = "v1"


def _artist(a: dict[str, Any]) -> dict[str, Any]:
    stats = a.get("statistics") or {}
    out = pick(a, "id", "artistName", "foreignArtistId", "status", "monitored", "artistType", "path")
    out.update({k: v for k, v in {
        "albums": stats.get("albumCount"),
        "tracks_on_disk": stats.get("trackFileCount"),
        "tracks_total": stats.get("totalTrackCount"),
        "size_gb": gib(stats.get("sizeOnDisk")),
    }.items() if v is not None})
    if a.get("overview") and "id" not in a:
        out["overview"] = a["overview"][:300]
    return out


def _album(al: dict[str, Any]) -> dict[str, Any]:
    out = pick(al, "id", "title", "releaseDate", "albumType", "monitored")
    out["artist"] = (al.get("artist") or {}).get("artistName")
    return out


def register(server: MCPServer, client: ArrClient, read_only: bool) -> None:
    register_common(server, client, "lidarr", {"includeArtist": True, "includeAlbum": True})

    @server.tool(name="lidarr_lookup_artist", annotations=READ,
                 description="Search for an artist to add (online lookup). Returns foreignArtistId "
                             "(MusicBrainz id) for lidarr_add_artist.")
    async def lookup_artist(term: str, limit: int = 10) -> list[dict[str, Any]]:
        return [_artist(a) for a in (await client.get("/artist/lookup", term=term))[:limit]]

    @server.tool(name="lidarr_list_artists", annotations=READ,
                 description="List artists in the Lidarr library, optionally filtered by name substring.")
    async def list_artists(name_contains: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        items = await client.get("/artist")
        if name_contains:
            items = [a for a in items if name_contains.lower() in (a.get("artistName") or "").lower()]
        return [_artist(a) for a in items[:limit]]

    @server.tool(name="lidarr_calendar", annotations=READ,
                 description="Albums releasing in a date window around today.")
    async def calendar(days_back: int = 7, days_ahead: int = 30) -> list[dict[str, Any]]:
        start, end = date_window(days_back, days_ahead)
        return [_album(a) for a in await client.get("/calendar", start=start, end=end, includeArtist=True)]

    @server.tool(name="lidarr_missing", annotations=READ,
                 description="Monitored albums that are released but not downloaded.")
    async def missing(page_size: int = 50) -> dict[str, Any]:
        data = await client.get("/wanted/missing", pageSize=page_size, includeArtist=True,
                                sortKey="releaseDate", sortDirection="descending")
        return {"total": data.get("totalRecords"), "albums": [_album(a) for a in data.get("records", [])]}

    @server.tool(name="lidarr_metadata_profiles", annotations=READ,
                 description="List Lidarr metadata profiles (ids are used when adding artists).")
    async def metadata_profiles() -> list[dict[str, Any]]:
        return [pick(p, "id", "name") for p in await client.get("/metadataprofile")]

    if read_only:
        return

    @server.tool(name="lidarr_add_artist", annotations=WRITE,
                 description="Add an artist to Lidarr by foreignArtistId (from lidarr_lookup_artist). "
                             "Uses the first quality/metadata profile and root folder unless given.")
    async def add_artist(
        foreign_artist_id: str,
        quality_profile_id: int | None = None,
        metadata_profile_id: int | None = None,
        root_folder_path: str | None = None,
        monitor: Literal["all", "future", "missing", "existing", "latest", "first", "none"] = "all",
        search_now: bool = True,
    ) -> dict[str, Any]:
        found = await client.get("/artist/lookup", term=f"lidarr:{foreign_artist_id}")
        if not found:
            raise ToolError(f"No artist found for id {foreign_artist_id}")
        artist = found[0]
        if artist.get("id"):
            return {"already_in_library": True, **_artist(artist)}
        qp, root = await resolve_defaults(client, quality_profile_id, root_folder_path)
        if metadata_profile_id is None:
            profiles = await client.get("/metadataprofile")
            if not profiles:
                raise ToolError("No metadata profiles configured")
            metadata_profile_id = profiles[0]["id"]
        artist.update({
            "qualityProfileId": qp, "metadataProfileId": metadata_profile_id, "rootFolderPath": root,
            "monitored": monitor != "none",
            "addOptions": {"monitor": monitor, "searchForMissingAlbums": search_now},
        })
        return {"added": True, **_artist(await client.post("/artist", artist))}

    @server.tool(name="lidarr_search_artist", annotations=WRITE,
                 description="Trigger Lidarr to search indexers for missing albums of a library artist (by Lidarr id).")
    async def search_artist(artist_id: int) -> dict[str, Any]:
        cmd = await client.post("/command", {"name": "ArtistSearch", "artistId": artist_id})
        return pick(cmd, "id", "name", "status")
