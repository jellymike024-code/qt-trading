param(
    [ValidateSet('wb','xhs','dy')][string[]]$Platforms = @('wb','xhs','dy'),
    [switch]$Automatic,
    [switch]$Smoke,
    [switch]$Foreground
)
$ErrorActionPreference = 'Stop'
$taskBase = Join-Path $PSScriptRoot 'integrations\BettaFish'
$taskPython = Join-Path $taskBase '.venv\Scripts\python.exe'
$taskScript = Join-Path $taskBase 'collector.py'
$taskRuntime = Join-Path $taskBase '.runtime'
if (-not (Test-Path -LiteralPath $taskPython)) { throw 'Collector environment is not installed.' }
if (Test-Path -LiteralPath (Join-Path $taskRuntime 'crawl.lock')) { throw 'A collector run is already active. Inspect data/bettafish/collector_status.json.' }
New-Item -ItemType Directory -Path $taskRuntime -Force | Out-Null
$taskArgs = @('crawl')
if ($Automatic) { $taskArgs += '--automatic' } else { $taskArgs += '--platforms'; $taskArgs += $Platforms }
if ($Smoke) { $taskArgs += '--smoke' }
if ($Foreground) {
    & $taskPython -E $taskScript @taskArgs
    if ($LASTEXITCODE -ne 0) { throw 'Collector failed; inspect integrations/BettaFish/.runtime logs.' }
} else {
    $taskQuotedScript = '"' + $taskScript + '"'
    $taskLaunchArgs = @('-E', $taskQuotedScript) + $taskArgs
    $taskProcess = Start-Process -FilePath $taskPython -ArgumentList $taskLaunchArgs -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $taskRuntime 'collector.log') -RedirectStandardError (Join-Path $taskRuntime 'collector-error.log')
    @{ pid=$taskProcess.Id; script=$taskScript; started_at=[DateTimeOffset]::Now.ToString('o') } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $taskRuntime 'process.json') -Encoding utf8
    Write-Output "Collector started, PID $($taskProcess.Id). First login uses a dedicated browser window."
}
