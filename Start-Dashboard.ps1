param([int]$Port = 8765, [switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$taskFrontend = Join-Path $PSScriptRoot 'frontend'
$taskUrl = "http://127.0.0.1:$Port"
$taskLogs = Join-Path $taskFrontend '.runtime'
New-Item -ItemType Directory -Path $taskLogs -Force | Out-Null
$taskReady = $false
try {
    $taskExisting = Invoke-RestMethod -Uri "$taskUrl/api/context" -TimeoutSec 2
    $taskReady = $taskExisting.quote_mode -eq 'real_sources_only'
} catch { }
if (-not $taskReady) {
    $taskPython = (Get-Command python -ErrorAction Stop).Source
    $taskServe = Join-Path $taskFrontend 'serve.py'
    $taskProcess = Start-Process -FilePath $taskPython -ArgumentList @('"' + $taskServe + '"', '--port', "$Port") -WorkingDirectory $taskFrontend -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $taskLogs 'server.log') -RedirectStandardError (Join-Path $taskLogs 'server-error.log')
    @{ pid = $taskProcess.Id; port = $Port; script = $taskServe; url = $taskUrl } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $taskLogs 'server.json') -Encoding utf8
    for ($taskAttempt = 0; $taskAttempt -lt 20; $taskAttempt++) {
        try {
            $taskResponse = Invoke-RestMethod -Uri "$taskUrl/api/context" -TimeoutSec 1
            if ($taskResponse.quote_mode -eq 'real_sources_only') { $taskReady = $true; break }
        } catch { Start-Sleep -Milliseconds 200 }
        if ($taskProcess.HasExited) { break }
    }
}
if (-not $taskReady) { throw "Dashboard failed to start. Inspect $taskLogs\server-error.log" }
Write-Output "ORBIT dashboard: $taskUrl"
if (-not $NoBrowser) { Start-Process $taskUrl }
