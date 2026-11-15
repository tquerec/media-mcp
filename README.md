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
| `MEDIA_MCP_HOST` / `MEDIA_MCP_PORT` | `127.0.0.1` / `8765` | only for `streamable-http` |
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

## Remote access (Claude mobile and other computers)

Claude's custom connectors (web, desktop and mobile) connect from Anthropic's cloud, so the server
needs a public HTTPS URL and a login. media-mcp has a built-in single-user OAuth login for this.

1. **Choose a password and hash it:** `uv run media-mcp hash-password`, then copy the printed
   `MEDIA_MCP_PASSWORD_HASH=...` line into `.env`.
2. **Put a tunnel in front of `127.0.0.1:8765`.** That way no router ports are opened. Either:
   - [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/)
     if you have a domain on Cloudflare: route a hostname to `http://127.0.0.1:8765`.
   - [Tailscale Funnel](https://tailscale.com/kb/1223/funnel): `tailscale funnel --bg 8765`.
3. **Set `MEDIA_MCP_PUBLIC_URL`** in `.env` to the tunnel's HTTPS URL (no trailing path), then restart
   the server (re-run the service installer on Windows).
4. **In Claude** › Settings › Connectors › *Add custom connector*, enter `<MEDIA_MCP_PUBLIC_URL>/mcp`.
   Claude opens the media-mcp sign-in page; enter your password. The connector then works on
   every device signed in to your Claude account.

How the login is protected:
- Only Claude's redirect hosts (plus localhost, for Claude Code) can register as clients.
  Change this with `MEDIA_MCP_ALLOWED_REDIRECT_HOSTS`.
- Access tokens last 1 hour. Refresh tokens last 30 days and change on every use. Only hashes
  of tokens are stored, in `MEDIA_MCP_STATE_FILE`.
- After 5 wrong passwords, the login is locked for 15 minutes.
- Delete the state file and restart to sign out every client.
- Consider `MEDIA_MCP_READ_ONLY=true` if you only need status from your phone.

The server refuses to listen on a non-loopback address without this login, unless you set
`MEDIA_MCP_ALLOW_UNAUTHENTICATED=true`.

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
