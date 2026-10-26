import copy
import json
from pathlib import Path

import pandas as pd
import pytest

from quantlab.engine import build_targets, equity, execute_open, mark_close, new_account, run_backtest, target_schedule


@pytest.fixture
def config():
    result = json.loads((Path(__file__).parents[1] / "config.json").read_text(encoding="utf-8"))
    result["symbols"] = ["510300"]
    result["execution"]["min_trade_value"] = 0
    return result


def bar(date, price=4., close=None, volume=100000000):
    close = price if close is None else close
    return pd.DataFrame([{"timestamp": pd.Timestamp(date + "T07:00:00Z"), "symbol": "510300",
                          "open": price, "close": close, "high": max(price, close),
                          "low": min(price, close), "volume": volume}])


def previous(price=4.):
    return {"510300": {"close": price, "volume": 100000000}}


def test_cash_lots_and_costs(config):
    account = new_account(config)
    execute_open(account, bar("2024-01-02"), previous(), {"510300": .25}, config)
    assert account["positions"]["510300"] % 100 == 0
    assert account["cash"] >= 0
    assert account["trades"][0]["price"] > 4
    mark_close(account, bar("2024-01-02"), config)
    assert equity(account) < config["initial_cash"]
    assert equity(account) == pytest.approx(account["cash"] + account["positions"]["510300"] * 4)


def test_t_plus_one_lock_and_next_day_sale(config):
    account = new_account(config)
    execute_open(account, bar("2024-01-02"), previous(), {"510300": .25}, config)
    execute_open(account, bar("2024-01-02"), previous(), {"510300": 0}, config)
    assert account["rejections"][-1]["reason"] == "T+1_locked"
    execute_open(account, bar("2024-01-03"), previous(), {"510300": 0}, config)
    assert account["positions"]["510300"] == 0


def test_raising_limit_and_no_print_session_reject(config):
    for frame, reason in [(bar("2024-01-02", 4.4), "opening_price_at_limit"),
                          (bar("2024-01-02", volume=0), "no_session_trades")]:
        account = new_account(config)
        execute_open(account, frame, previous(), {"510300": .25}, config)
        assert not account["trades"]
        assert account["rejections"][-1]["reason"] == reason


def test_risk_halt_does_not_promise_drawdown_cap(config):
    account = new_account(config)
    account["positions"] = {"510300": 200000}
    account["cash"] = 200000
    account["marks"] = {"510300": 4.}
    # A 20% overnight drop exceeds the 12% portfolio budget. Limit-down also
    # prevents immediate liquidation; the simulator must report the loss.
    day = bar("2024-01-03", 3.2)
    execute_open(account, day, previous(), {"510300": .25}, config)
    mark_close(account, day, config)
    assert account["halted"]
    assert account["equity"][-1]["drawdown"] > .12
    assert account["positions"]["510300"] == 200000


def test_signal_executes_only_on_following_session(config, monkeypatch):
    bars = pd.concat([bar("2024-01-02"), bar("2024-01-03"), bar("2024-01-04")], ignore_index=True)
    schedule = {stamp: {"510300": weight} for stamp, weight in zip(bars.timestamp, [0., .25, 0.])}
    monkeypatch.setattr("quantlab.engine.target_schedule", lambda *args: schedule)
    account = run_backtest(bars, "ma_trend", config, bars.timestamp.min())
    assert len(account["trades"]) == 1
    assert account["trades"][0]["session"] == "2024-01-04"
    assert pd.Timestamp(account["trades"][0]["timestamp"]).hour == 1  # 09:30 China


def test_compiled_targets_match_incremental_and_are_order_invariant(config):
    dates = pd.bdate_range("2023-01-02", periods=150, tz="UTC") + pd.Timedelta(hours=7)
    records = [{"timestamp": t, "symbol": "510300", "open": 3+i*.005, "close": 3+i*.005,
                "high": 3.001+i*.005, "low": 2.999+i*.005, "volume": 100000000} for i, t in enumerate(dates)]
    bars = pd.DataFrame(records)
    for strategy in config["strategies"] + ["ensemble", "ensemble_sentiment", "buy_hold"]:
        compiled = target_schedule(bars, strategy, config)
        account = new_account(config)
        targets, _ = build_targets(bars.sample(frac=1, random_state=11), strategy, config, account)
        assert targets == pytest.approx(compiled[dates[-1]])
        prefix = target_schedule(bars.iloc[:-5], strategy, config)
        assert prefix[dates[-6]] == pytest.approx(compiled[dates[-6]])
