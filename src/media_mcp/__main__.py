"""Entry point: ``media-mcp`` or ``python -m media_mcp``."""

from __future__ import annotations

import sys

from .config import load_config
from .server import build_server, check_exposure, transport_security


def _hash_password() -> None:
    import getpass

    from .auth import hash_password

    pw = getpass.getpass("New password: ")
    if len(pw) < 12:
        sys.exit("Use at least 12 characters.")
    if getpass.getpass("Again: ") != pw:
        sys.exit("Passwords did not match.")
    print(f"MEDIA_MCP_PASSWORD_HASH={hash_password(pw)}")


def main() -> None:
    if sys.argv[1:2] == ["hash-password"]:
        _hash_password()
        return
    config = load_config()
    check_exposure(config)
    enabled = ", ".join(config.services) or "none"
    mode = "read-only" if config.read_only else "read-write"
    # stdout is the MCP channel for stdio, so log to stderr.
    print(f"media-mcp: services: {enabled} ({mode})", file=sys.stderr)
    if config.public_url:
        print(f"media-mcp: remote access enabled with password login at {config.public_url}/mcp", file=sys.stderr)

    server = build_server(config)
    if config.transport == "streamable-http":
        server.run("streamable-http", host=config.host, port=config.port,
                   transport_security=transport_security(config))
    elif config.transport == "stdio":
        server.run("stdio")
    else:
        sys.exit(f"media-mcp: unknown MEDIA_MCP_TRANSPORT {config.transport!r} (use stdio or streamable-http)")


if __name__ == "__main__":
    main()
