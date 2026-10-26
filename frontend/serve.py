"""Loopback-only static frontend and bounded read-only research snapshots."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from urllib.parse import unquote, urlsplit

BASE = Path(__file__).resolve().parent
WORKSPACE = BASE.parent
DIST = BASE / "dist"


def read_json(relative, default):
    try:
        return json.loads((WORKSPACE / relative).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def context():
    summary = read_json("reports/summary.json", {})
    paper = read_json("state/paper.json", {})
    status = read_json("data/social/status.json", {})
    candidates = read_json("data/social/candidates.json", {})
    analysis = read_json("reports/social_analysis.json", {})
    candidate_ids = {r.get('id') for r in candidates.get('items', [])}
    curve = []
    try:
        with (WORKSPACE / "reports/holdout/ensemble_equity.csv").open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        curve = [{"timestamp": row["timestamp"], "equity": float(row["equity"])} for row in rows[-120:]]
    except (OSError, KeyError, ValueError):
        pass
    return {
        "served_at": datetime.now(timezone.utc).isoformat(), "quote_mode": "real_sources_only",
        "social": {"status": status, "items": candidates.get("items", []),
                   "analysis": [r for r in analysis.get("items", []) if r.get('record_id') in candidate_ids]},
        "research": {key: summary.get(key) for key in ("provider", "latest_session", "holdout_start", "holdout")},
        "paper": {key: paper.get(key) for key in ("status", "started_at", "expires_at", "signals", "metrics")},
        "equity": curve, "markets": read_json("data/markets/latest.json", {}),
        "bettafish": {"status": read_json("data/bettafish/status.json", {"status":"not_connected","comments":0}),
                      "dashboard": read_json("data/bettafish/dashboard.json", {}),
                      "collector": read_json("data/bettafish/collector_status.json", {})},
        "configuration": {key: read_json("config.json", {}).get(key) for key in ("initial_cash", "risk")},
    }


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DIST), **kwargs)

    def do_GET(self):
        path = unquote(urlsplit(self.path).path)
        if path == "/api/context":
            payload = json.dumps(context(), ensure_ascii=False, allow_nan=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        super().do_GET()

    def translate_path(self, path):
        path = unquote(urlsplit(path).path)
        if path in ('/collector/login/wb.png','/collector/login/xhs.png','/collector/login/dy.png'):
            return str(WORKSPACE/'integrations/BettaFish/.runtime'/('login-'+Path(path).name))
        if path.startswith("/research/"):
            reports = (WORKSPACE / "reports").resolve()
            target = (reports / path[len("/research/"):]).resolve()
            if not target.is_relative_to(reports):
                return str(DIST / "__forbidden__")
            return str(target)
        target = Path(super().translate_path(path)).resolve()
        return str(target) if target.is_relative_to(DIST.resolve()) else str(DIST / "__forbidden__")

    def list_directory(self, path):
        self.send_error(403, "Directory listing disabled")
        return None

    def end_headers(self):
        if self.path.startswith('/collector/login/'):
            self.send_header('Cache-Control','no-store')
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        super().end_headers()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1", choices=["127.0.0.1", "0.0.0.0"])
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"ORBIT dashboard: http://127.0.0.1:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
