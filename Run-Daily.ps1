$ErrorActionPreference = 'Stop'

function Test-QuantlabDailyCycleReady {
    param([DateTimeOffset]$Now = [DateTimeOffset]::UtcNow)
    try {
        $taskShanghaiZone = [TimeZoneInfo]::FindSystemTimeZoneById('China Standard Time')
    } catch {
        $taskShanghaiZone = [TimeZoneInfo]::FindSystemTimeZoneById('Asia/Shanghai')
    }
    $taskShanghaiNow = [TimeZoneInfo]::ConvertTime($Now, $taskShanghaiZone)
    return $taskShanghaiNow.TimeOfDay -ge [TimeSpan]::new(15, 10, 0)
}

Set-Location -LiteralPath $PSScriptRoot
python -m quantlab.markets collect
if ($LASTEXITCODE -ne 0) { Write-Warning 'Public market collector failed; missing quotes stay empty. Inspect data/markets/latest.json.' }
python -m quantlab.social collect
if ($LASTEXITCODE -ne 0) { Write-Warning 'Public sentiment collection failed. Inspect reports/social_status.json.' }
$taskCollectorPython = Join-Path $PSScriptRoot 'integrations\BettaFish\.venv\Scripts\python.exe'
if (Test-Path -LiteralPath $taskCollectorPython) {
    & $taskCollectorPython -E (Join-Path $PSScriptRoot 'integrations\BettaFish\collector.py') crawl --automatic
    if ($LASTEXITCODE -ne 0) { Write-Warning 'Comment collection needs attention. Inspect data/bettafish/collector_status.json.' }
}
python -m quantlab.bettafish sync
if ($LASTEXITCODE -ne 0) { Write-Warning 'BettaFish export synchronization failed. Inspect reports/bettafish_status.json.' }
if (-not (Test-QuantlabDailyCycleReady)) {
    Write-Output 'Daily cycle deferred until 15:10 Asia/Shanghai; public market and sentiment collection completed. Frozen paper ledger unchanged.'
    return
}
python -m quantlab cycle
if ($LASTEXITCODE -ne 0) {
    $taskFailure = Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot 'state/status.json') | ConvertFrom-Json
    if ($taskFailure.error -notlike '*Tencent raw/adjusted dates differ:*') {
        throw 'Quantlab cycle failed; source access errors are not retried through another route. Inspect state/status.json.'
    }
    Write-Warning 'Tencent date mismatch; verify the full old history before attempting a same-source append.'
    python -m quantlab.refresh_market --apply
    if ($LASTEXITCODE -ne 0) { throw 'Verified same-source refresh failed; existing ledger and pending targets were preserved.' }
    python -m quantlab paper
    if ($LASTEXITCODE -ne 0) { throw 'Paper update failed after input verification; inspect state/status.json.' }
    python -m quantlab research
    if ($LASTEXITCODE -ne 0) { throw 'Research refresh failed; inspect state/status.json.' }
}