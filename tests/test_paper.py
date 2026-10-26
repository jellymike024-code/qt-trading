import copy
import json
from pathlib import Path

import pandas as pd
import pytest

from quantlab.paper import update_paper


@pytest.fixture
def case():
    config = json.loads((Path(__file__).parents[1] / "config.json").read_text(encoding="utf-8"))
    dates = pd.bdate_range("2024-01-02", periods=151, tz="UTC") + pd.Timedelta(hours=7)
    rows = []
    for s in config["symbols"]:
        for i, t in enumerate(dates):
            p = 3 + i * .005
            rows.append({"timestamp": t, "symbol": s, "open": p, "high": p+.001,
                         "low": p-.001, "close": p, "volume": 100000000})
    return config, pd.DataFrame(rows).sort_values(["timestamp", "symbol"]).reset_index(drop=True), dates


def test_start_does_not_backfill_and_restart_does_not_duplicate(case):
    config, bars, dates = case
    first = bars[bars.timestamp <= dates[-2]]
    clock = dates[-2] + pd.Timedelta(hours=1)
    state = update_paper(first, config, "fixed", clock, [])
    assert all(not a["trades"] and a["cash"] == 1000000 for a in state["accounts"].values())
    second = update_paper(bars, config, "fixed", dates[-1]+pd.Timedelta(hours=1), [], state)
    counts = {s: len(a["trades"]) for s, a in second["accounts"].items()}
    assert counts["ensemble"] > 0
    third = update_paper(bars, config, "fixed", dates[-1]+pd.Timedelta(hours=2), [], second)
    assert counts == {s: len(a["trades"]) for s, a in third["accounts"].items()}
    for a in third["accounts"].values():
        assert len(a["equity"]) == 1


def test_retry_without_new_bar_preserves_frozen_generation_time(case):
    config, bars, dates = case
    first = bars[bars.timestamp <= dates[-2]]
    clock = dates[-2]+pd.Timedelta(hours=1)
    state = update_paper(first, config, "fixed", clock, [])
    frozen = copy.deepcopy(state["signals"])
    state = update_paper(first, config, "fixed", clock+pd.Timedelta(hours=18), [], state)
    assert state["signals"] == frozen
    assert all(a["pending_generated_at"] == str(clock) for a in state["accounts"].values())


def test_skipped_sessions_are_marked_without_hindsight_trades(case):
    config, bars, dates = case
    state = update_paper(bars[bars.timestamp <= dates[-3]], config, "fixed", dates[-3]+pd.Timedelta(hours=1), [])
    state = update_paper(bars, config, "fixed", dates[-1]+pd.Timedelta(hours=1), [], state)
    assert all(not a["trades"] for a in state["accounts"].values())
    assert state["warnings"]


def test_stale_future_and_changed_version_refused(case):
    config, bars, dates = case
    clock = dates[-1] + pd.Timedelta(hours=1)
    with pytest.raises(ValueError, match="陈旧"):
        update_paper(bars, config, "fixed", clock + pd.Timedelta(days=10), [])
    with pytest.raises(ValueError, match="future"):
        update_paper(bars, config, "fixed", dates[-1]-pd.Timedelta(hours=1), [])
    state = update_paper(bars, config, "fixed", clock, [])
    with pytest.raises(ValueError, match="变化"):
        update_paper(bars, config, "changed", clock, [], state)


def test_expiry_stops_new_targets(case):
    config, bars, dates = case
    clock = dates[-1]+pd.Timedelta(hours=1)
    state = update_paper(bars, config, "fixed", clock, [])
    state["expires_at"] = str(clock - pd.Timedelta(seconds=1))
    state = update_paper(bars, config, "fixed", clock, [], state)
    assert state["status"] == "completed_review_required"
    assert all(not a["pending"] for a in state["accounts"].values())
