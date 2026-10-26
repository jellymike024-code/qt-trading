$ErrorActionPreference = 'Stop'
$taskBase = Join-Path $PSScriptRoot 'integrations\BettaFish'
$taskState = Join-Path $taskBase '.runtime\process.json'
if (Test-Path -LiteralPath $taskState) {
    $taskRecorded = Get-Content -LiteralPath $taskState -Raw | ConvertFrom-Json
    $taskManager = Get-CimInstance Win32_Process -Filter "ProcessId = $($taskRecorded.pid)" -ErrorAction SilentlyContinue
    if ($taskManager -and $taskManager.CommandLine -and $taskManager.CommandLine.Contains([string]$taskRecorded.script)) {
        $taskRunner = Join-Path $taskBase 'runner.py'
        Get-CimInstance Win32_Process -Filter "ParentProcessId = $($taskRecorded.pid)" | Where-Object { $_.CommandLine -and $_.CommandLine.Contains($taskRunner) } | ForEach-Object { Stop-Process -Id $_.ProcessId }
        Stop-Process -Id $taskRecorded.pid
        $taskLock = Join-Path $taskBase '.runtime\crawl.lock'
        if (Test-Path -LiteralPath $taskLock) { Remove-Item -LiteralPath $taskLock }
        Write-Output 'Stopped the recorded collector processes. Database and login profiles were retained.'
    } else { Write-Output 'The recorded collector is no longer running. No process was stopped.' }
}
