"""SABnzbd (download client) tools."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from ..client import SabClient
from ._common import READ, WRITE, pick


def register(server: MCPServer, client: SabClient, read_only: bool) -> None:

    @server.tool(name="sabnzbd_status", annotations=READ,
                 description="Current SABnzbd status: paused or downloading, speed, time left, disk space, "
                             "and the active queue.")
    async def status(limit: int = 20) -> dict[str, Any]:
        q = (await client.call("queue", limit=limit)).get("queue", {})
        return {
            "status": q.get("status"),
            "paused": q.get("paused"),
            "speed": q.get("speed"),
            "size_left": q.get("sizeleft"),
            "time_left": q.get("timeleft"),
            "queue_count": q.get("noofslots_total"),
            "disk_free_gb": q.get("diskspace1"),
            "speed_limit": q.get("speedlimit"),
            "items": [{
                "id": s.get("nzo_id"), "name": s.get("filename"), "status": s.get("status"),
                "category": s.get("cat"), "progress_pct": s.get("percentage"),
                "size": s.get("size"), "size_left": s.get("sizeleft"), "time_left": s.get("timeleft"),
            } for s in q.get("slots", [])],
        }

    @server.tool(name="sabnzbd_history", annotations=READ,
                 description="Recently finished or failed SABnzbd downloads.")
    async def history(limit: int = 20, failed_only: bool = False) -> dict[str, Any]:
        h = (await client.call("history", limit=limit, failed_only=1 if failed_only else None)).get("history", {})
        return {
            "total": h.get("noofslots"),
            "items": [{
                **pick(s, "name", "status", "category", "size", "fail_message"),
                "completed": s.get("completed"),
            } for s in h.get("slots", [])],
        }

    @server.tool(name="sabnzbd_warnings", annotations=READ,
                 description="Current SABnzbd warnings and errors.")
    async def warnings() -> list[Any]:
        return (await client.call("warnings")).get("warnings", [])

    if read_only:
        return

    @server.tool(name="sabnzbd_pause", annotations=WRITE,
                 description="Pause the whole SABnzbd queue, optionally for a number of minutes.")
    async def pause(minutes: int | None = None) -> dict[str, Any]:
        if minutes:
            await client.call("config", name="set_pause", value=minutes)
        else:
            await client.call("pause")
        return {"paused": True, "minutes": minutes}

    @server.tool(name="sabnzbd_resume", annotations=WRITE,
                 description="Resume the SABnzbd queue.")
    async def resume() -> dict[str, Any]:
        await client.call("resume")
        return {"paused": False}
