param([ValidateSet('wb','xhs','dy')][string]$Platform='wb')
$ErrorActionPreference = 'Stop'
$taskBase = Join-Path $PSScriptRoot 'integrations\BettaFish'
if (Test-Path -LiteralPath (Join-Path $taskBase '.runtime\crawl.lock')) {
    $taskStatusFile = Join-Path $PSScriptRoot 'data\bettafish\collector_status.json'
    $taskStatus = Get-Content -LiteralPath $taskStatusFile -Raw | ConvertFrom-Json
    if ($taskStatus.active_platform -eq $Platform) { throw 'Stop this platform collector before manual login.' }
}
$taskConfig = Get-Content -LiteralPath (Join-Path $taskBase 'collector_config.json') -Raw | ConvertFrom-Json
$taskProfile = Join-Path $taskBase ('browser_profiles\' + $Platform)
New-Item -ItemType Directory -Path $taskProfile -Force | Out-Null
$taskSites = @{ wb='https://weibo.com/'; xhs='https://www.xiaohongshu.com/explore'; dy='https://www.douyin.com/' }
$taskProfileArgument = '--user-data-dir="' + $taskProfile + '"'
# A normal user-controlled window: no debugging port and no page automation.
Start-Process -FilePath $taskConfig.browser_path -ArgumentList @($taskProfileArgument, '--new-window', $taskSites[$Platform])
Write-Output 'Manual login browser opened. After login, close this dedicated window before starting the collector.'
