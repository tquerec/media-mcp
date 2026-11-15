"""Tests against a fake HTTP backend; no real services or credentials are used."""

from __future__ import annotations

import json

import httpx
import pytest

from mcp.server.mcpserver.exceptions import ToolError

from media_mcp.config import load_config
from media_mcp.server import build_server

FAKE_KEY = "test-key-not-real"


def fake_backend(request: httpx.Request) -> httpx.Response:
    path, q = request.url.path, request.url.params
    if path.startswith("/api/v"):
        assert request.headers["X-Api-Key"] == FAKE_KEY
    routes = {
        "/api/v3/system/status": {"appName": "Sonarr", "version": "4.0.0"},
        "/api/v3/health": [{"type": "warning", "source": "IndexerCheck", "message": "No indexers"}],
        "/api/v3/qualityprofile": [{"id": 4, "name": "HD-1080p"}],
        "/api/v3/rootfolder": [{"path": "/data/tv", "freeSpace": 2 * 1024**3, "accessible": True}],
        "/api/v3/queue": {"totalRecords": 1, "records": [
            {"id": 1, "title": "Example.S01E01", "status": "downloading", "size": 100, "sizeleft": 25}]},
        "/api/v3/series/lookup": [{"title": "Example Show", "year": 2020, "tvdbId": 123, "overview": "x"}],
        "/api/v1/artist": [{"id": 1, "artistName": "Example Band", "statistics": {"albumCount": 3}}],
    }
    if path == "/api/v3/series" and request.method == "POST":
        body = json.loads(request.content)
        assert body["qualityProfileId"] == 4 and body["rootFolderPath"] == "/data/tv"
        assert body["addOptions"] == {"monitor": "all", "searchForMissingEpisodes": True}
        return httpx.Response(201, json={**body, "id": 99})
    if path == "/api":
        assert q["apikey"] == FAKE_KEY
        if q["mode"] == "queue":
            return httpx.Response(200, json={"queue": {
                "status": "Downloading", "paused": False, "speed": "5.0 M", "timeleft": "0:10:00",
                "noofslots_total": 1, "slots": [{"nzo_id": "SABnzbd_nzo_1", "filename": "Example",
                                                 "percentage": "40", "status": "Downloading"}]}})
        if q["mode"] == "pause":
            return httpx.Response(200, json={"status": True})
    if path in routes:
        return httpx.Response(200, json=routes[path])
    return httpx.Response(404, text="not found")


def make(env: dict[str, str]):
    return build_server(load_config(env=env), transport=httpx.MockTransport(fake_backend))


async def tool_names(server) -> set[str]:
    return {t.name for t in await server.list_tools()}


async def call(server, name, **args):
    result = await server.call_tool(name, args)
    assert not result.is_error, result
    return result.structured_content


def test_config_only_enables_services_with_keys():
    cfg = load_config(env={"SONARR_API_KEY": "secret-value", "RADARR_URL": "http://localhost:1"})
    assert list(cfg.services) == ["sonarr"]
    assert cfg.services["sonarr"].url == "http://localhost:8989"
    assert "secret-value" not in repr(cfg.services["sonarr"])


async def test_only_configured_groups_are_registered():
    names = await tool_names(make({"SONARR_API_KEY": FAKE_KEY}))
    assert "sonarr_queue" in names and "media_services" in names
    assert not any(n.startswith(("radarr_", "lidarr_", "sabnzbd_")) for n in names)


async def test_read_only_hides_write_tools():
    names = await tool_names(make({"SONARR_API_KEY": FAKE_KEY, "SABNZBD_API_KEY": FAKE_KEY,
                                   "MEDIA_MCP_READ_ONLY": "true"}))
    assert "sonarr_lookup_series" in names and "sabnzbd_status" in names
    assert "sonarr_add_series" not in names and "sabnzbd_pause" not in names


async def test_sonarr_tools():
    server = make({"SONARR_API_KEY": FAKE_KEY})
    status = await call(server, "sonarr_system_status")
    assert status["status"]["version"] == "4.0.0"
    assert status["health"][0]["source"] == "IndexerCheck"
    queue = await call(server, "sonarr_queue")
    assert queue["items"][0]["progress_pct"] == 75.0
    added = await call(server, "sonarr_add_series", tvdb_id=123)
    assert added["added"] and added["id"] == 99


async def test_lidarr_uses_v1_api():
    server = make({"LIDARR_API_KEY": FAKE_KEY})
    result = await call(server, "lidarr_list_artists")
    assert result["result"][0]["artistName"] == "Example Band"


async def test_sabnzbd_status_and_pause():
    server = make({"SABNZBD_API_KEY": FAKE_KEY})
    status = await call(server, "sabnzbd_status")
    assert status["status"] == "Downloading" and status["items"][0]["progress_pct"] == "40"
    assert (await call(server, "sabnzbd_pause"))["paused"] is True


async def test_errors_are_reported_not_raised():
    server = make({"RADARR_API_KEY": FAKE_KEY})
    with pytest.raises(ToolError, match="radarr: HTTP 404"):
        await server.call_tool("radarr_list_movies", {})
