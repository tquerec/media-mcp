<#
.SYNOPSIS
    Installs (or reinstalls) media-mcp as a Windows service using WinSW.

.DESCRIPTION
    - Builds the Python environment with uv (.venv in the repo).
    - Downloads a pinned, hash-checked WinSW into .\service (gitignored).
    - Registers a service that runs media-mcp over streamable HTTP on 127.0.0.1,
      reading API keys from the repo's .env file.
    - Starts the service and checks that the MCP endpoint answers.

    Run from an elevated PowerShell:
        powershell -ExecutionPolicy Bypass -File scripts\windows\install-service.ps1

    Re-run it after `git pull` to pick up code changes.
#>
#Requires -RunAsAdministrator
[CmdletBinding()]
param(
    [string]$ServiceName = "media-mcp",
    [switch]$SkipEnvPermissions
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$WinSWVersion = "v2.12.0"
$WinSWUrl     = "https://github.com/winsw/winsw/releases/download/$WinSWVersion/WinSW-x64.exe"
$WinSWSha256  = "05B82D46AD331CC16BDC00DE5C6332C1EF818DF8CEEFCD49C726553209B3A0DA"

$UvVersion = "0.13.8"
$UvUrl     = "https://github.com/astral-sh/uv/releases/download/$UvVersion/uv-x86_64-pc-windows-msvc.zip"
$UvSha256  = "529E78A9CEC6CD7E3E014F7ECD8BDA0C113969D4ED0F2EFEEDB85C30943325B8"

$RepoRoot   = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$EnvFile    = Join-Path $RepoRoot ".env"
$ServiceDir = Join-Path $RepoRoot "service"
$WrapperExe = Join-Path $ServiceDir "$ServiceName-service.exe"
$WrapperXml = Join-Path $ServiceDir "$ServiceName-service.xml"
$ServerExe  = Join-Path $RepoRoot ".venv\Scripts\media-mcp.exe"

function Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }

# --- Preconditions --------------------------------------------------------
if (-not (Test-Path $EnvFile)) {
    throw "No .env at $EnvFile. Copy .env.example to .env and fill in your API keys first."
}
# Find uv: PATH first, then the usual per-user install locations (an elevated
# shell often lacks the user's PATH), else download a pinned, hash-checked copy
# into .tools\uv (gitignored).
$uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
if (-not $uv) {
    $candidates = @(
        (Join-Path $env:USERPROFILE ".local\bin\uv.exe"),
        (Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Links\uv.exe"),
        (Join-Path $env:USERPROFILE ".cargo\bin\uv.exe"),
        (Join-Path $RepoRoot ".tools\uv\uv.exe")
    )
    $wingetPkgs = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages"
    if (Test-Path $wingetPkgs) {
        $candidates += Get-ChildItem $wingetPkgs -Directory -Filter "astral-sh.uv*" -ErrorAction SilentlyContinue |
            ForEach-Object { Join-Path $_.FullName "uv.exe" }
    }
    $uv = $candidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
}
if (-not $uv) {
    Step "uv not found; downloading uv $UvVersion into .tools\uv"
    $uvDir = Join-Path $RepoRoot ".tools\uv"
    New-Item -ItemType Directory -Force -Path $uvDir | Out-Null
    $zip = Join-Path $uvDir "uv.zip"
    Invoke-WebRequest -Uri $UvUrl -OutFile $zip -UseBasicParsing
    $hash = (Get-FileHash $zip -Algorithm SHA256).Hash
    if ($hash -ne $UvSha256) { Remove-Item $zip -Force; throw "uv download hash mismatch ($hash). Aborting." }
    Expand-Archive -Path $zip -DestinationPath $uvDir -Force
    Remove-Item $zip -Force
    $uv = Join-Path $uvDir "uv.exe"
    if (-not (Test-Path $uv)) { throw "uv.exe not found after extracting to $uvDir" }
}
Step "Using uv at $uv"

# Read host/port from .env so the health check hits the right place.
$envValues = @{}
Get-Content $EnvFile | ForEach-Object {
    if ($_ -match '^\s*([A-Z0-9_]+)\s*=\s*(.*?)\s*$') { $envValues[$Matches[1]] = $Matches[2].Trim('"', "'") }
}
$BindHost = if ($envValues["MEDIA_MCP_HOST"]) { $envValues["MEDIA_MCP_HOST"] } else { "127.0.0.1" }
$Port     = if ($envValues["MEDIA_MCP_PORT"]) { [int]$envValues["MEDIA_MCP_PORT"] } else { 8765 }
$CheckHost = if ($BindHost -eq "0.0.0.0") { "127.0.0.1" } else { $BindHost }

# --- Stop an existing install so files are not locked ----------------------
if (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue) {
    Step "Stopping existing $ServiceName service"
    if (Test-Path $WrapperExe) {
        & $WrapperExe stop | Out-Null
        & $WrapperExe uninstall | Out-Null
    } else {
        Stop-Service $ServiceName -Force -ErrorAction SilentlyContinue
        sc.exe delete $ServiceName | Out-Null
    }
    Start-Sleep -Seconds 2
}

# --- Python environment ---------------------------------------------------
Step "Installing Python dependencies with uv"
Push-Location $RepoRoot
try {
    # Keep the interpreter inside the repo so the service account can always reach it.
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $RepoRoot ".python"
    & $uv sync --no-dev --frozen --python-preference only-managed
    if ($LASTEXITCODE -ne 0) { throw "uv sync failed" }
} finally { Pop-Location }
if (-not (Test-Path $ServerExe)) { throw "Expected $ServerExe after uv sync, but it is missing." }

# --- WinSW ----------------------------------------------------------------
New-Item -ItemType Directory -Force -Path $ServiceDir, (Join-Path $ServiceDir "logs") | Out-Null
$needDownload = -not (Test-Path $WrapperExe) -or ((Get-FileHash $WrapperExe -Algorithm SHA256).Hash -ne $WinSWSha256)
if ($needDownload) {
    Step "Downloading WinSW $WinSWVersion"
    $tmp = "$WrapperExe.download"
    Invoke-WebRequest -Uri $WinSWUrl -OutFile $tmp -UseBasicParsing
    $hash = (Get-FileHash $tmp -Algorithm SHA256).Hash
    if ($hash -ne $WinSWSha256) { Remove-Item $tmp -Force; throw "WinSW hash mismatch ($hash). Aborting." }
    Move-Item -Force $tmp $WrapperExe
}

Step "Writing service definition"
$esc = { param($s) [System.Security.SecurityElement]::Escape($s) }
@"
<service>
  <id>$(& $esc $ServiceName)</id>
  <name>Media MCP</name>
  <description>MCP server for Sonarr, Radarr, Lidarr and SABnzbd (streamable HTTP).</description>
  <executable>$(& $esc $ServerExe)</executable>
  <workingdirectory>$(& $esc $RepoRoot)</workingdirectory>
  <env name="MEDIA_MCP_TRANSPORT" value="streamable-http" />
  <env name="MEDIA_MCP_ENV_FILE" value="$(& $esc $EnvFile)" />
  <env name="PYTHONUNBUFFERED" value="1" />
  <startmode>Automatic</startmode>
  <delayedAutoStart>true</delayedAutoStart>
  <onfailure action="restart" delay="10 sec" />
  <onfailure action="restart" delay="30 sec" />
  <onfailure action="restart" delay="60 sec" />
  <resetfailure>1 hour</resetfailure>
  <logpath>$(& $esc (Join-Path $ServiceDir "logs"))</logpath>
  <log mode="roll-by-size">
    <sizeThreshold>10240</sizeThreshold>
    <keepFiles>5</keepFiles>
  </log>
</service>
"@ | Set-Content -Path $WrapperXml -Encoding UTF8

# --- Protect the .env file ------------------------------------------------
if (-not $SkipEnvPermissions) {
    Step "Restricting .env to SYSTEM, Administrators and $env:USERNAME"
    icacls $EnvFile /inheritance:r /grant:r "*S-1-5-18:(F)" "*S-1-5-32-544:(F)" "$($env:USERDOMAIN)\$($env:USERNAME):(F)" | Out-Null
}

# --- Install and start ----------------------------------------------------
Step "Installing and starting the $ServiceName service"
& $WrapperExe install
if ($LASTEXITCODE -ne 0) { throw "WinSW install failed" }
& $WrapperExe start
if ($LASTEXITCODE -ne 0) { throw "WinSW start failed; see $ServiceDir\logs" }

# --- Health check ---------------------------------------------------------
$url = "http://${CheckHost}:$Port/mcp"
Step "Checking $url"
$body = '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"install-check","version":"1"}}}'
$ok = $false
foreach ($i in 1..20) {
    try {
        $r = Invoke-WebRequest -Uri $url -Method Post -Body $body -UseBasicParsing -TimeoutSec 5 `
            -ContentType "application/json" -Headers @{ Accept = "application/json, text/event-stream" }
        if ($r.Content -match '"serverInfo"') { $ok = $true; break }
    } catch { }
    Start-Sleep -Seconds 1
}
if (-not $ok) { throw "Service started but $url did not answer. Check the logs in $ServiceDir\logs." }

Write-Host ""
Write-Host "media-mcp is running as the '$ServiceName' service at $url" -ForegroundColor Green
Write-Host "Connect a client to $url (see the README's 'Run as a Windows service' section)."
