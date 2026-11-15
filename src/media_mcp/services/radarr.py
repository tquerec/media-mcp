"""Radarr (movies) tools."""

from __future__ import annotations

from typing import Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from ..client import ArrClient
from ._common import READ, WRITE, date_window, gib, pick, register_common, resolve_defaults

API_VERSION = "v3"


def _movie(m: dict[str, Any]) -> dict[str, Any]:
    out = pick(m, "id", "title", "year", "tmdbId", "imdbId", "status", "monitored", "hasFile",
               "minimumAvailability", "path")
    if m.get("sizeOnDisk"):
        out["size_gb"] = gib(m["sizeOnDisk"])
    if m.get("overview") and "id" not in m:
        out["overview"] = m["overview"][:300]
    return out


def register(server: MCPServer, client: ArrClient, read_only: bool) -> None:
    register_common(server, client, "radarr", {"includeMovie": True})

    @server.tool(name="radarr_lookup_movie", annotations=READ,
                 description="Search for a movie to add (online lookup). Returns tmdbId for radarr_add_movie.")
    async def lookup_movie(term: str, limit: int = 10) -> list[dict[str, Any]]:
        return [_movie(m) for m in (await client.get("/movie/lookup", term=term))[:limit]]

    @server.tool(name="radarr_list_movies", annotations=READ,
                 description="List movies in the Radarr library, optionally filtered by title substring or missing files.")
    async def list_movies(title_contains: str | None = None, missing_only: bool = False,
                          limit: int = 100) -> list[dict[str, Any]]:
        items = await client.get("/movie")
        if title_contains:
            items = [m for m in items if title_contains.lower() in (m.get("title") or "").lower()]
        if missing_only:
            items = [m for m in items if m.get("monitored") and not m.get("hasFile")]
        return [_movie(m) for m in items[:limit]]

    @server.tool(name="radarr_calendar", annotations=READ,
                 description="Movies with cinema, digital or physical releases in a date window around today.")
    async def calendar(days_back: int = 7, days_ahead: int = 30) -> list[dict[str, Any]]:
        start, end = date_window(days_back, days_ahead)
        movies = await client.get("/calendar", start=start, end=end)
        return [pick(m, "title", "year", "inCinemas", "digitalRelease", "physicalRelease", "hasFile", "monitored")
                for m in movies]

    if read_only:
        return

    @server.tool(name="radarr_add_movie", annotations=WRITE,
                 description="Add a movie to Radarr by tmdbId (from radarr_lookup_movie). "
                             "Uses the first quality profile/root folder unless given.")
    async def add_movie(
        tmdb_id: int,
        quality_profile_id: int | None = None,
        root_folder_path: str | None = None,
        minimum_availability: Literal["announced", "inCinemas", "released"] = "released",
        search_now: bool = True,
    ) -> dict[str, Any]:
        movie = await client.get("/movie/lookup/tmdb", tmdbId=tmdb_id)
        if not movie:
            raise ToolError(f"No movie found for tmdbId {tmdb_id}")
        if movie.get("id"):
            return {"already_in_library": True, **_movie(movie)}
        qp, root = await resolve_defaults(client, quality_profile_id, root_folder_path)
        movie.update({
            "qualityProfileId": qp, "rootFolderPath": root, "monitored": True,
            "minimumAvailability": minimum_availability,
            "addOptions": {"searchForMovie": search_now},
        })
        return {"added": True, **_movie(await client.post("/movie", movie))}

    @server.tool(name="radarr_search_movie", annotations=WRITE,
                 description="Trigger Radarr to search indexers for a library movie (by Radarr id).")
    async def search_movie(movie_id: int) -> dict[str, Any]:
        cmd = await client.post("/command", {"name": "MoviesSearch", "movieIds": [movie_id]})
        return pick(cmd, "id", "name", "status")
