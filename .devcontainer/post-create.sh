#!/usr/bin/env bash
set -euo pipefail
python -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
# Full crawler dependencies and Chromium stay inside the private codespace.
python -m venv integrations/BettaFish/.venv
integrations/BettaFish/.venv/bin/python -m pip install -r integrations/BettaFish/requirements-cloud.txt
integrations/BettaFish/.venv/bin/python -m playwright install --with-deps chromium
.venv/bin/python -m deploy.bootstrap_collector
integrations/BettaFish/.venv/bin/python -E integrations/BettaFish/collector.py init
echo 'Cloud collector initialized. Platform sessions start empty; no local ledger was imported.'
