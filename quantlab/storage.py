from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    os.replace(temporary, path)


def experiment_hash(root, config):
    digest = hashlib.sha256(json.dumps(config, sort_keys=True).encode())
    for name in ("engine.py", "strategies.py", "sentiment.py", "paper.py", "data.py"):
        digest.update((Path(root) / "quantlab" / name).read_bytes())
    return digest.hexdigest()


def validate_config(config):
    risk, ex = config["risk"], config["execution"]
    if config["initial_cash"] <= 0 or not config["symbols"] or len(set(config["symbols"])) != len(config["symbols"]):
        raise ValueError("invalid capital or symbol list")
    if not 0 < risk["soft_drawdown"] < risk["hard_drawdown"] < risk["max_drawdown_budget"] < 1:
        raise ValueError("drawdown thresholds must be increasing, below budget")
    if not 0 < risk["max_gross_weight"] <= 1 or not 0 < risk["max_symbol_weight"] <= 1:
        raise ValueError("cash-only weights must be in (0,1]")
    if ex["lot_size"] != 100 or any(ex[k] < 0 for k in ("commission_rate", "min_commission", "slippage_bps")):
        raise ValueError("invalid ETF lot size or cost")
    if any(s not in ex["limit_pct"] for s in config["symbols"]):
        raise ValueError("configure per-symbol price limits")
    return config
