"""Causal, long-only ETF research signals computed on completed daily bars.

Scores describe desired exposure, not an executable order. The caller must apply
risk sizing and execute no earlier than the next trading session. All rolling
statistics use only the current and earlier observations of the same symbol.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

STRATEGIES = ("ma_trend", "donchian", "ts_momentum", "volume_momentum")

_MIN_HISTORY = {
    "ma_trend": 60,
    "donchian": 56,
    "ts_momentum": 121,
    "volume_momentum": 60,
}


def min_history(strategy: str) -> int:
    """Return observations required before the first potentially nonzero score."""
    if strategy not in _MIN_HISTORY:
        raise ValueError(f"Unknown strategy {strategy!r}; choose one of {STRATEGIES}")
    return _MIN_HISTORY[strategy]


def _symbol_scores(frame: pd.DataFrame, strategy: str) -> np.ndarray:
    close = frame["signal_close"].astype(float) if "signal_close" in frame else frame["close"].astype(float)
    close = close.reset_index(drop=True)
    volume = frame["volume"].astype(float).reset_index(drop=True)

    if strategy == "ma_trend":
        fast = close.rolling(20, min_periods=20).mean()
        slow = close.rolling(60, min_periods=60).mean()
        scores = ((fast > slow) & (close > slow)).astype(float)
    elif strategy == "ts_momentum":
        # Three fixed horizons reduce dependence on one arbitrary lookback.
        returns = pd.concat([close.pct_change(n, fill_method=None) for n in (20, 60, 120)], axis=1)
        scores = (returns > 0).mean(axis=1)
        scores.loc[returns.isna().any(axis=1)] = 0.0
    elif strategy == "volume_momentum":
        slow = close.rolling(60, min_periods=60).mean()
        momentum = close.pct_change(20, fill_method=None)
        # Compare today's completed volume to *preceding* sessions only.
        previous_volume = volume.shift(1).rolling(20, min_periods=20).mean()
        confirmed = volume >= previous_volume * 1.05
        trending = (close > slow) & (momentum > 0)
        scores = trending.astype(float) * (0.5 + 0.5 * confirmed.astype(float))
    else:
        # Apply the close adjustment ratio to OHLC so dividend/split adjustment
        # does not put the channel and signal close on different price scales.
        adjustment = close / frame["close"].astype(float).reset_index(drop=True)
        high = frame["high"].astype(float).reset_index(drop=True) * adjustment
        low = frame["low"].astype(float).reset_index(drop=True) * adjustment
        entry = high.shift(1).rolling(55, min_periods=55).max()
        exit_ = low.shift(1).rolling(20, min_periods=20).min()
        held = False
        values = np.zeros(len(frame), dtype=float)
        for i in range(len(frame)):
            if pd.notna(entry.iat[i]):
                if close.iat[i] < exit_.iat[i]:
                    held = False
                elif close.iat[i] > entry.iat[i]:
                    held = True
            values[i] = float(held)
        scores = pd.Series(values)

    # A zero-volume bar cannot create a new positive instruction. This also
    # prevents a suspended instrument from getting a fresh buy recommendation.
    return scores.where(volume > 0, 0.0).fillna(0.0).clip(0.0, 1.0).to_numpy()


def generate_signals(bars: pd.DataFrame, strategy: str) -> pd.DataFrame:
    """Return symbol/timestamp/score rows in precisely the input row order.

    ``signal_close`` may carry adjusted prices used solely for indicators;
    execution/accounting must still use the unadjusted OHLC columns. Adjustment
    provenance and corporate-action accounting are responsibilities of the data
    provider and execution engine.
    """
    min_history(strategy)
    required = {"timestamp", "symbol", "open", "high", "low", "close", "volume"}
    missing = required - set(bars.columns)
    if missing:
        raise ValueError(f"Missing bar columns: {sorted(missing)}")
    result = bars.loc[:, ["symbol", "timestamp"]].copy()
    if bars.empty:
        result["score"] = pd.Series(index=result.index, dtype=float)
        return result
    if bars["symbol"].isna().any() or bars["timestamp"].isna().any():
        raise ValueError("symbol and timestamp cannot be missing")
    if bars.duplicated(["symbol", "timestamp"]).any():
        raise ValueError("Duplicate symbol/timestamp bars are ambiguous")
    numeric = ["open", "high", "low", "close", "volume"]
    if "signal_close" in bars:
        numeric.append("signal_close")
    values = bars[numeric].to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise ValueError("Prices and volume must be finite")
    prices = [column for column in numeric if column != "volume"]
    if (bars[prices].astype(float) <= 0).any().any() or (bars["volume"].astype(float) < 0).any():
        raise ValueError("Prices must be positive and volume must be nonnegative")

    ordered = bars.copy()
    ordered["_position"] = np.arange(len(bars))
    ordered["_time"] = pd.to_datetime(ordered["timestamp"], utc=True, errors="raise")
    ordered = ordered.sort_values(["symbol", "_time"], kind="stable")
    scores = np.zeros(len(bars), dtype=float)
    for _, frame in ordered.groupby("symbol", sort=False):
        scores[frame["_position"].to_numpy()] = _symbol_scores(frame, strategy)
    result["score"] = scores
    return result
