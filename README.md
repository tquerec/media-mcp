# media-mcp

One [Model Context Protocol](https://modelcontextprotocol.io) server for a home media stack:

| Service | What you can do |
|---|---|
| **Sonarr** (TV) | look up and add series, list library, calendar, missing episodes, queue, trigger searches |
| **Radarr** (movies) | look up and add movies, list library, calendar, queue, trigger searches |
| **Lidarr** (music) | look up and add artists, list library, calendar, missing albums, queue, trigger searches |
| **SABnzbd** (downloads) | current status and queue, history, warnings, pause/resume |

It is designed to run on the same machine as those apps and talk to them over `localhost`.

## Why one server?

Each service is a separate *tool group* that is only switched on when its API key is set. You get one
process and one config file, and you can still run with any subset (say, just Sonarr and SABnzbd).
Tool names are prefixed (`sonarr_*`, `radarr_*`, `lidarr_*`, `sabnzbd_*`) so the model can tell them apart.

## Install

Requires Python 3.10+. With [uv](https://docs.astral.sh/uv/):

```sh
git clone https://github.com/<you>/media-mcp.git
cd media-mcp
uv sync
```

or with pip: `pip install .`

## Configure

All configuration is via environment variables. The easiest way is a local `.env` file,
which is gitignored so your keys never end up in the repo:

```sh
cp .env.example .env
# then edit .env and paste in your API keys
```

| Variable | Default | Notes |
|---|---|---|
| `SONARR_URL` / `SONARR_API_KEY` | `http://localhost:8989` | Sonarr › Settings › General |
| `RADARR_URL` / `RADARR_API_KEY` | `http://localhost:7878` | Radarr › Settings › General |
| `LIDARR_URL` / `LIDARR_API_KEY` | `http://localhost:8686` | Lidarr › Settings › General |
| `SABNZBD_URL` / `SABNZBD_API_KEY` | `http://localhost:8080` | SABnzbd › Config › General |
| `MEDIA_MCP_READ_ONLY` | `false` | `true` hides every tool that changes something |
| `MEDIA_MCP_TIMEOUT` | `30` | HTTP timeout in seconds |
| `MEDIA_MCP_TRANSPORT` | `stdio` | `stdio` or `streamable-http` |
| `MEDIA_MCP_HOST` / `MEDIA_MCP_PORT` | `127.0.0.1` / `8765` | only for `streamable-http`; `0.0.0.0` for LAN access |
| `MEDIA_MCP_BEARER_TOKEN` | | required for LAN access; `uv run media-mcp new-token` |
| `MEDIA_MCP_ENV_FILE` | `./.env` | path to an alternative env file |

A service with no API key is simply disabled. Variables already set in the environment win over `.env`.

## Use with Claude

**Claude Code** (from the repo directory):

```sh
claude mcp add media -- uv run --directory /path/to/media-mcp media-mcp
```

**Claude Desktop**: add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "media": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/media-mcp", "media-mcp"],
      "env": { "MEDIA_MCP_ENV_FILE": "/path/to/media-mcp/.env" }
    }
  }
}
```

**Over HTTP**: set `MEDIA_MCP_TRANSPORT=streamable-http` and run `uv run media-mcp`; the endpoint is
`http://127.0.0.1:8765/mcp`. This has no authentication of its own, so keep it bound to `127.0.0.1`
or put it behind something that does authenticate.

## Run as a Windows service

To keep the server running in the background on Windows, install it as a service. It then
serves streamable HTTP on `127.0.0.1` (port `MEDIA_MCP_PORT`, default `8765`) and starts with Windows.

1. In the repo folder: `copy .env.example .env`, then add your API keys to `.env`.
2. From an **elevated** PowerShell in the repo folder:

   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\windows\install-service.ps1
   ```

The script finds [uv](https://docs.astral.sh/uv/) (or downloads a pinned, hash-checked copy into `.tools\`), builds the environment (Python is kept inside the repo in `.python\`), downloads a
pinned, hash-checked [WinSW](https://github.com/winsw/winsw) into `service\`, registers a `media-mcp`
service that restarts on failure, restricts `.env` to SYSTEM, Administrators and you, and checks that
the endpoint answers. Logs are in `service\logs\`. Re-run it after `git pull` to update.
To remove the service: `scripts\windows\uninstall-service.ps1` (also elevated).

Then connect Claude Code with:

```sh
claude mcp add --transport http media http://127.0.0.1:8765/mcp
```

For Claude Desktop, which launches stdio servers, bridge to the HTTP endpoint with
[`mcp-remote`](https://www.npmjs.com/package/mcp-remote):
`{"command": "npx", "args": ["-y", "mcp-remote", "http://127.0.0.1:8765/mcp"]}`.

## Use from other computers on your network

The server can be shared with other devices on your home network. It isn't reachable from the internet.

1. Generate a token: `uv run media-mcp new-token`. Put the printed line in `.env`, along with
   `MEDIA_MCP_HOST=0.0.0.0`.
2. Re-run the Windows service installer. It adds a firewall rule that allows the port only from
   your local subnet, and only on networks marked Private or Domain. Without a token, the server
   refuses to listen beyond localhost.
3. On the other computer, connect **Claude Code**. Replace `MEDIA-PC` with the server's name or
   IP address (the installer prints it) and use the token from `.env`:

   ```sh
   claude mcp add --transport http media http://MEDIA-PC:8765/mcp \
     --header "Authorization: Bearer <your MEDIA_MCP_BEARER_TOKEN>"
   ```

   Add `--scope user` to make it available in every project. Check it with `claude mcp list`.

   **Claude Desktop** can't send headers to an HTTP server directly. Install
   [Node.js](https://nodejs.org) and bridge with `mcp-remote` in `claude_desktop_config.json`
   (Settings › Developer › Edit Config):

   ```json
   {
     "mcpServers": {
       "media": {
         "command": "npx",
         "args": ["-y", "mcp-remote@0.15.2", "http://MEDIA-PC:8765/mcp",
                  "--allow-http", "--header", "Authorization:${AUTH_HEADER}"],
         "env": { "AUTH_HEADER": "Bearer <your MEDIA_MCP_BEARER_TOKEN>" }
       }
     }
   }
   ```

Traffic on the LAN is plain HTTP. Anyone on your network who can capture traffic could read the
token, so only do this on a network you trust.

Claude on mobile and claude.ai custom connectors connect from Anthropic's cloud, not from your
device, so they can't reach a server that is only on your LAN.

## Tools

Call `media_services` to see which groups are active.

- **Common to Sonarr/Radarr/Lidarr:** `*_system_status` (version + health), `*_queue`,
  `*_quality_profiles`, `*_root_folders`
- **Sonarr:** `sonarr_lookup_series`, `sonarr_list_series`, `sonarr_calendar`, `sonarr_missing`,
  `sonarr_add_series`✎, `sonarr_search_series`✎
- **Radarr:** `radarr_lookup_movie`, `radarr_list_movies`, `radarr_calendar`,
  `radarr_add_movie`✎, `radarr_search_movie`✎
- **Lidarr:** `lidarr_lookup_artist`, `lidarr_list_artists`, `lidarr_calendar`, `lidarr_missing`,
  `lidarr_metadata_profiles`, `lidarr_add_artist`✎, `lidarr_search_artist`✎
- **SABnzbd:** `sabnzbd_status`, `sabnzbd_history`, `sabnzbd_warnings`, `sabnzbd_pause`✎, `sabnzbd_resume`✎

✎ = changes something; hidden when `MEDIA_MCP_READ_ONLY=true`. Nothing in this server deletes media.

## Development

```sh
uv sync --extra dev
uv run pytest
```

Tests use a fake HTTP backend, so no real services or keys are needed.

## Security notes

- Keys are read only from the environment / `.env`; they are never logged or returned by tools.
- `.env` and other local config files are in `.gitignore`. Don't commit them.
- The API keys give full control of each app, so treat the machine running this server accordingly.

## License

MIT
