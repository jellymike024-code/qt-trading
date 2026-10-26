"""Persisted daily forward paper ledger. Never opens historical trades on init."""
from __future__ import annotations

import pandas as pd

from .engine import build_targets, execute_open, mark_close, metrics, new_account


def opening_time(timestamp):
    return timestamp.tz_convert("Asia/Shanghai").normalize() + pd.Timedelta(hours=9, minutes=30)


def update_paper(bars, config, fingerprint, now, events, state=None):
    now = pd.Timestamp(now)
    latest = bars.timestamp.max()
    if (now.tz_convert("Asia/Shanghai").date() - latest.tz_convert("Asia/Shanghai").date()).days > config["max_data_age_days"]:
        raise ValueError("行情陈旧：暂停模拟盘更新和新信号")
    if latest > now:
        raise ValueError("cannot use future/unclosed bars")
    if state and state["fingerprint"] != fingerprint:
        raise ValueError("模拟实验参数或代码已变化：禁止混写；请保留现有state并另建实验目录")
    strategies = config["strategies"] + ["ensemble", "ensemble_sentiment", "buy_hold"]
    if state is None:
        state = {"version": 1, "fingerprint": fingerprint, "started_at": str(now),
                 "expires_at": str(now.tz_convert("Asia/Shanghai").normalize()
                                   + pd.DateOffset(months=config["paper_months"])
                                   + pd.Timedelta(hours=15, minutes=10)),
                 "last_session": str(latest), "last_updated": str(now), "status": "running",
                 "mode": "daily_indicative_paper", "warnings": [],
                 "accounts": {s: new_account(config) for s in strategies}, "signals": {}}
        # Starting cash is observed now. All prior bars are warmup only.
        for account in state["accounts"].values():
            account["marks"] = {r.symbol: float(r.close) for r in bars[bars.timestamp == latest].itertuples()}
        new_days = []
    else:
        new_days = sorted(bars.loc[bars.timestamp > pd.Timestamp(state["last_session"]), "timestamp"].unique())
    expires = pd.Timestamp(state["expires_at"])
    if state.get("signals") and not new_days and now < expires:
        # Keep a previously frozen order across restarts and premarket retries.
        # Recomputing it after the next opening time would lose causality.
        return state
    for strategy, account in state["accounts"].items():
        for session in new_days:
            if session > expires:
                continue
            day = bars[bars.timestamp == session]
            prior = bars[bars.timestamp < session].sort_values("timestamp").groupby("symbol").tail(1)
            previous = {r.symbol: r._asdict() for r in prior.itertuples(index=False)}
            generated = pd.Timestamp(account["pending_generated_at"]) if account.get("pending_generated_at") else now
            # Missed multiple sessions: never reconstruct trades with hindsight.
            may_fill = (len(new_days) == 1 and account.get("pending_date") == state["last_session"]
                        and generated < opening_time(session) and session <= expires)
            if may_fill:
                execute_open(account, day, previous, account["pending"], config)
            elif account["pending"]:
                account["rejections"].append({"timestamp": str(session), "reason": "missed_or_unfrozen_session"})
            mark_close(account, day, config)
        if now < expires:
            account["pending"], detail = build_targets(bars, strategy, config, account, events, now)
            account["pending_date"] = str(latest)
            account["pending_generated_at"] = str(now)
            state["signals"][strategy] = {"generated_at": str(now), "based_on_session": str(latest),
                                          "execution": "next_session_open_estimate", "targets": detail}
        else:
            account["pending"] = {}
    if len(new_days) > 1:
        state["warnings"].append({"at": str(now), "message": "漏跑多个交易日：已更新估值，未追补交易"})
    if now >= expires:
        state["status"] = "completed_review_required"
        state["signals"] = {}
    state["last_session"] = str(latest)
    state["last_updated"] = str(now)
    state["metrics"] = {s: metrics(a, config) for s, a in state["accounts"].items()}
    return state
