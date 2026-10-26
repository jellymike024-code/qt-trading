$ErrorActionPreference = 'Stop'
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
python -m quantlab cycle
if ($LASTEXITCODE -ne 0) { throw 'Quantlab cycle failed; inspect state/status.json.' }
