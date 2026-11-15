"""Entry point: ``media-mcp`` or ``python -m media_mcp``."""

from __future__ import annotations

import sys

from .config import load_config
from .server import build_server


def main() -> None:
    config = load_config()
    enabled = ", ".join(config.services) or "none"
    mode = "read-only" if config.read_only else "read-write"
    # stdout is the MCP channel for stdio, so log to stderr.
    print(f"media-mcp: services: {enabled} ({mode})", file=sys.stderr)

    server = build_server(config)
    if config.transport == "streamable-http":
        server.run("streamable-http", host=config.host, port=config.port)
    elif config.transport == "stdio":
        server.run("stdio")
    else:
        sys.exit(f"media-mcp: unknown MEDIA_MCP_TRANSPORT {config.transport!r} (use stdio or streamable-http)")


if __name__ == "__main__":
    main()
