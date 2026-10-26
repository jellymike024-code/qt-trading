"""Causal daily-bar cash accounting. All orders use already frozen weights.

Daily OHLC execution is an indicative model, not a claim of exchange fills.
No current-day high/low/volume is used to decide an opening fill.
"""
from __future__ import annotations

import copy
import math
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP

import numpy as np
import pandas as pd

from .sentiment import evaluate_events
from .strategies import generate_signals


def new_account(config: dict) -> dict:
    cash = float(config["initial_cash"])
    return {"cash": cash, "positions": {}, "last_buy": {}, "marks": {},
            "peak": cash, "halted": False, "pending": {}, "pending_date": None,
            "equity": [], "trades": [], "rejections": []}


def equity(account: dict, prices: dict | None = None) -> float:
    marks = account["marks"] | (prices or {})
    return float(account["cash"] + sum(q * marks[s] for s, q in account["positions"].items() if q))


def drawdown(account: dict, value: float | None = None) -> float:
    value = equity(account) if value is None else value
    return max(0., 1. - value / max(account["peak"], 1e-12))


def risk_scale(account: dict, config: dict) -> float:
    risk = config["risk"]
    dd = drawdown(account)
    if account["halted"] or dd >= risk["hard_drawdown"]:
        account["halted"] = True
        return 0.
    return 0.5 if dd >= risk["soft_drawdown"] else 1.


def build_targets(history: pd.DataFrame, strategy: str, config: dict,
                  account: dict, events: list | None = None,
                  asof: pd.Timestamp | None = None) -> tuple[dict, dict]:
    """Only closed bars through asof may be passed. Equal sleeves, vol capped."""
    asof = pd.Timestamp(asof if asof is not None else history.timestamp.max())
    history = history[history.timestamp <= asof].sort_values(["symbol", "timestamp"])
    n = len(config["symbols"])
    targets, details = {}, {}
    scale = risk_scale(account, config)
    modes = config["strategies"] if strategy.startswith("ensemble") else [strategy]
    generated = [] if strategy == "buy_hold" else [generate_signals(history, x) for x in modes]
    for symbol in config["symbols"]:
        bars = history[history.symbol == symbol].sort_values("timestamp")
        if bars.empty or bars.timestamp.iloc[-1] != history.timestamp.max():
            targets[symbol] = 0.
            details[symbol] = {"status": "missing_bar", "score": 0., "weight": 0.}
            continue
        if strategy == "buy_hold":
            score = 1.
        else:
            score = float(np.mean([f.loc[f.symbol == symbol, "score"].iloc[-1] for f in generated]))
        prices = bars.get("signal_close", bars.close)
        vol = float(prices.pct_change(fill_method=None).tail(20).std(ddof=1) * np.sqrt(252))
        vol_scale = min(1., config["risk"]["target_annual_vol"] / max(vol, 0.01)) if math.isfinite(vol) else 0.
        news = {"multiplier": 1., "status": "disabled", "event_ids": []}
        if strategy == "ensemble_sentiment":
            news = evaluate_events(events or [], symbol, asof, config["news_max_age_hours"])
        weight = min(config["risk"]["max_symbol_weight"],
                     config["risk"]["max_gross_weight"] / n * score * vol_scale) * scale * news["multiplier"]
        targets[symbol] = float(weight)
        details[symbol] = {"score": score, "annual_vol": vol if math.isfinite(vol) else None,
                           "weight": weight, "news": news}
    return targets, details


def _rounded(price: float, tick: float, side: str) -> float:
    mode = ROUND_CEILING if side == "BUY" else ROUND_FLOOR
    return float((Decimal(str(price)) / Decimal(str(tick))).quantize(Decimal("1"), rounding=mode) * Decimal(str(tick)))


def _limit(price: float, tick: float) -> float:
    return float((Decimal(str(price)) / Decimal(str(tick))).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * Decimal(str(tick)))


def execute_open(account: dict, day: pd.DataFrame, previous: dict,
                 targets: dict, config: dict) -> None:
    """Sell before buy; positions bought today are unavailable for same-day sales."""
    timestamp = day.timestamp.iloc[0]
    date = str(timestamp.tz_convert("Asia/Shanghai").date())
    rows = {r.symbol: r for r in day.itertuples()}
    opens = {s: float(r.open) for s, r in rows.items()}
    value = equity(account, opens)
    if value <= 0:
        account["halted"] = True
    if drawdown(account, value) >= config["risk"]["hard_drawdown"]:
        account["halted"] = True
    targets = {s: 0. for s in config["symbols"]} if account["halted"] else targets
    ex = config["execution"]
    lot, tick = ex["lot_size"], ex["price_tick"]
    orders = []
    for s in config["symbols"]:
        if s not in rows or s not in previous:
            continue
        desired = int(max(value, 0.) * targets.get(s, 0.) / opens[s] / lot) * lot
        orders.append((s, desired - account["positions"].get(s, 0)))
    for s, delta in sorted(orders, key=lambda item: item[1]):
        if delta == 0:
            continue
        side = "BUY" if delta > 0 else "SELL"
        prev = previous[s]
        def reject(reason):
            account["rejections"].append({"timestamp": str(timestamp), "symbol": s, "side": side, "reason": reason})
        if side == "SELL" and account["last_buy"].get(s) == date:
            reject("T+1_locked")
            continue
        # A complete session with no prints cannot contain an opening fill.
        # This is a matching constraint, never a feature or liquidity forecast.
        if float(rows[s].volume) == 0:
            reject("no_session_trades")
            continue
        # Prior-session volume only: current day's final volume is not yet known.
        capacity = int(float(prev["volume"]) * ex["max_previous_volume_participation"] / lot) * lot
        qty = min(abs(delta), capacity)
        if not qty:
            reject("previous_volume_unavailable")
            continue
        limit_pct = ex["limit_pct"][s]
        upper = _limit(float(prev["close"]) * (1 + limit_pct), tick)
        lower = _limit(float(prev["close"]) * (1 - limit_pct), tick)
        if side == "BUY" and opens[s] >= upper or side == "SELL" and opens[s] <= lower:
            reject("opening_price_at_limit")
            continue
        price = _rounded(opens[s] * (1 + (1 if side == "BUY" else -1) * ex["slippage_bps"] / 10000), tick, side)
        if price > upper or price < lower:
            reject("slippage_beyond_price_limit")
            continue
        if side == "BUY":
            max_qty = int(max(0., account["cash"] - ex["min_commission"]) / (price * (1 + ex["commission_rate"])) / lot) * lot
            qty = min(qty, max_qty)
        if qty == 0 or qty * price < ex["min_trade_value"] and targets.get(s, 0.) > 0:
            continue
        fee = max(ex["min_commission"], qty * price * ex["commission_rate"])
        signed = qty if side == "BUY" else -qty
        account["cash"] -= signed * price + fee
        account["positions"][s] = account["positions"].get(s, 0) + signed
        if side == "BUY":
            account["last_buy"][s] = date
        account["trades"].append({"timestamp": str(timestamp.normalize() + pd.Timedelta(hours=1, minutes=30)),
                                  "session": date, "symbol": s, "side": side,
                                  "quantity": qty, "price": price, "fee": fee,
                                  "notional": qty * price, "model": "next_open_estimate"})
    if account["cash"] < -1e-6 or any(x < 0 or x % lot for x in account["positions"].values()):
        raise RuntimeError("cash/long-only/lot invariant failed")


def mark_close(account: dict, day: pd.DataFrame, config: dict) -> None:
    account["marks"].update({r.symbol: float(r.close) for r in day.itertuples()})
    value = equity(account)
    account["peak"] = max(account["peak"], value)
    dd = drawdown(account, value)
    if dd >= config["risk"]["hard_drawdown"]:
        account["halted"] = True
    invested = value - account["cash"]
    account["equity"].append({"timestamp": str(day.timestamp.iloc[0]), "equity": value,
                              "cash": account["cash"], "market_value": invested,
                              "drawdown": dd, "exposure": invested / value if value > 0 else 0.,
                              "halted": account["halted"]})


def target_schedule(bars: pd.DataFrame, strategy: str, config: dict, events=None) -> dict:
    """Vectorize causal features once; sequential risk/accounting stays event based."""
    ordered = bars.sort_values(["symbol", "timestamp"]).reset_index(drop=True)
    modes = config["strategies"] if strategy.startswith("ensemble") else [strategy]
    scores = np.ones(len(ordered)) if strategy == "buy_hold" else np.mean(
        [generate_signals(ordered, mode).score.to_numpy() for mode in modes], axis=0)
    weights = np.zeros(len(ordered))
    for _, group in ordered.groupby("symbol", sort=False):
        price = group.get("signal_close", group.close)
        vol = price.pct_change(fill_method=None).rolling(20, min_periods=2).std(ddof=1) * np.sqrt(252)
        sizing = (config["risk"]["target_annual_vol"] / vol.clip(lower=0.01)).clip(upper=1).fillna(0)
        idx = group.index.to_numpy()
        weights[idx] = np.minimum(config["risk"]["max_symbol_weight"],
            config["risk"]["max_gross_weight"] / len(config["symbols"]) * scores[idx] * sizing.to_numpy())
    if strategy == "ensemble_sentiment" and events:
        for i, row in enumerate(ordered.itertuples()):
            weights[i] *= evaluate_events(events, row.symbol, row.timestamp, config["news_max_age_hours"])["multiplier"]
    ordered["weight"] = weights
    return {timestamp: dict(zip(day.symbol, day.weight)) for timestamp, day in ordered.groupby("timestamp", sort=True)}


def run_backtest(bars: pd.DataFrame, strategy: str, config: dict,
                 start: pd.Timestamp, events: list | None = None) -> dict:
    account = new_account(config)
    previous = {}
    schedule = target_schedule(bars, strategy, config, events)
    previous_timestamp = None
    for timestamp, day in bars.groupby("timestamp", sort=True):
        if timestamp < start:
            previous.update({r.symbol: r._asdict() for r in day.itertuples(index=False)})
            previous_timestamp = timestamp
            continue
        if not account["pending"] and not account["equity"]:
            if previous_timestamp is not None:
                account["pending"] = schedule[previous_timestamp]
        execute_open(account, day, previous, account["pending"], config)
        mark_close(account, day, config)
        scale = risk_scale(account, config)
        account["pending"] = {s: w * scale for s, w in schedule[timestamp].items()}
        account["pending_date"] = str(timestamp)
        previous.update({r.symbol: r._asdict() for r in day.itertuples(index=False)})
    return account


def metrics(account: dict, config: dict) -> dict:
    curve = pd.DataFrame(account["equity"])
    if curve.empty:
        return {"sessions": 0, "total_return": 0., "max_drawdown": 0., "sharpe": None,
                "annualized_return": None, "fees": 0., "fills": 0, "turnover": 0., "mean_exposure": 0.}
    values = np.r_[config["initial_cash"], curve.equity.to_numpy()]
    returns = values[1:] / values[:-1] - 1
    std = float(np.std(returns, ddof=1)) if len(returns) > 1 else 0.
    total = values[-1] / values[0] - 1
    return {"sessions": len(curve), "total_return": float(total),
            "annualized_return": float((1 + total) ** (252 / len(curve)) - 1) if len(curve) >= 60 and total > -1 else None,
            "max_drawdown": float(curve.drawdown.max()),
            "sharpe": float(np.mean(returns) / std * np.sqrt(252)) if std > 1e-12 else None,
            "fees": float(sum(t["fee"] for t in account["trades"])),
            "fills": len(account["trades"]),
            "turnover": float(sum(t["notional"] for t in account["trades"]) / config["initial_cash"]),
            "mean_exposure": float(curve.exposure.mean())}


def stress_config(config: dict) -> dict:
    result = copy.deepcopy(config)
    for key in ("commission_rate", "min_commission", "slippage_bps"):
        result["execution"][key] *= 2
    return result
