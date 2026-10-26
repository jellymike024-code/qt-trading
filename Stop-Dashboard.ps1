$ErrorActionPreference = 'Stop'
$taskState = Join-Path $PSScriptRoot 'frontend\.runtime\server.json'
if (-not (Test-Path -LiteralPath $taskState)) { Write-Output 'No dashboard process was recorded.'; return }
$taskServer = Get-Content -LiteralPath $taskState -Raw | ConvertFrom-Json
$taskProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $($taskServer.pid)" -ErrorAction SilentlyContinue
if ($taskProcess -and $taskProcess.CommandLine -and $taskProcess.CommandLine.Contains([string]$taskServer.script)) {
    Stop-Process -Id $taskServer.pid
    Write-Output 'Stopped the recorded ORBIT dashboard server.'
} else { Write-Output 'The recorded dashboard process is no longer running. No process was stopped.' }
