<#
.SYNOPSIS
    Stops and removes the media-mcp Windows service. Leaves the repo, .env and logs in place.
#>
#Requires -RunAsAdministrator
[CmdletBinding()]
param([string]$ServiceName = "media-mcp")

$ErrorActionPreference = "Stop"
$RepoRoot   = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$WrapperExe = Join-Path $RepoRoot "service\$ServiceName-service.exe"

if (-not (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue)) {
    Write-Host "Service '$ServiceName' is not installed."
    return
}
if (Test-Path $WrapperExe) {
    & $WrapperExe stop | Out-Null
    & $WrapperExe uninstall
} else {
    Stop-Service $ServiceName -Force -ErrorAction SilentlyContinue
    sc.exe delete $ServiceName
}
Get-NetFirewallRule -DisplayName "media-mcp (LAN*" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
Write-Host "Removed service '$ServiceName'." -ForegroundColor Green
