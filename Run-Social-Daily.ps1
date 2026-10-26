$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
python -m quantlab.social collect
if ($LASTEXITCODE -ne 0) { throw 'Social collection failed; inspect reports/social_status.json.' }
