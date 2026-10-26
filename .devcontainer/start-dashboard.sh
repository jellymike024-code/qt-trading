#!/usr/bin/env bash
set -euo pipefail
mkdir -p frontend/.runtime
if ! curl --fail --silent http://127.0.0.1:8765/api/context >/dev/null; then
  nohup .venv/bin/python frontend/serve.py --host 0.0.0.0 --port 8765 >frontend/.runtime/codespaces-dashboard.log 2>&1 < /dev/null &
fi
