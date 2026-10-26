"""Daily ETF bars: validated CSVs, public Eastmoney history, and explicit demo data.

Prices in ``open/high/low/close`` are always unadjusted execution prices.
``signal_close`` is an optional adjusted feature and must never be a fill price.
All timestamps denote the Shanghai 15:00 close and are stored as UTC.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import requests


BAR_COLUMNS = ["timestamp", "symbol", "open", "high", "low", "close", "volume"]
PRICE_COLUMNS = ["open", "high", "low", "close"]
EASTMONEY_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"


def _now_shanghai() -> pd.Timestamp:
    return pd.Timestamp.now(tz="Asia/Shanghai")


def _close_timestamp(value: object) -> pd.Timestamp:
    if pd.isna(value):
        raise ValueError("timestamp must not be empty")
    text = str(value).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}|\d{8}", text):
        stamp = pd.Timestamp(text).tz_localize("Asia/Shanghai") + pd.Timedelta(hours=15)
    else:
        stamp = pd.Timestamp(value)
        if stamp.tzinfo is None:
            stamp = stamp.tz_localize("Asia/Shanghai")
        stamp = stamp.tz_convert("Asia/Shanghai")
        if stamp != stamp.normalize() + pd.Timedelta(hours=15):
            raise ValueError("daily timestamp must represent 15:00 Asia/Shanghai")
    return stamp.tz_convert("UTC")


def _validate_bars(bars: pd.DataFrame, *, now: pd.Timestamp | None = None) -> pd.DataFrame:
    missing = set(BAR_COLUMNS) - set(bars.columns)
    if missing:
        raise ValueError(f"missing bar columns: {', '.join(sorted(missing))}")
    if bars.empty:
        raise ValueError("bar data is empty")
    columns = BAR_COLUMNS + (["signal_close"] if "signal_close" in bars.columns else [])
    result = bars.loc[:, columns].copy()
    # Do not stringify numbers here: a lost leading zero must be detected, not repaired.
    if not result["symbol"].map(lambda value: isinstance(value, str) and bool(re.fullmatch(r"\d{6}", value))).all():
        raise ValueError("symbol must be a six-digit string")
    try:
        result["timestamp"] = pd.to_datetime(result["timestamp"].map(_close_timestamp), utc=True)
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError(f"invalid daily timestamp: {exc}") from exc
    if result.duplicated(["timestamp", "symbol"]).any():
        raise ValueError("duplicate timestamp/symbol bars")
    numeric_columns = PRICE_COLUMNS + ["volume"] + (["signal_close"] if "signal_close" in result else [])
    for column in numeric_columns:
        try:
            result[column] = pd.to_numeric(result[column], errors="raise")
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{column} must be numeric") from exc
        if not np.isfinite(result[column].to_numpy(dtype=float)).all():
            raise ValueError(f"{column} must contain finite values")
    price_columns = PRICE_COLUMNS + (["signal_close"] if "signal_close" in result else [])
    if (result[price_columns] <= 0).any().any():
        raise ValueError("prices must be strictly positive")
    if (result["volume"] < 0).any():
        raise ValueError("volume must be nonnegative shares")
    if (result["high"] < result[["open", "close", "low"]].max(axis=1)).any():
        raise ValueError("high must be at least open, close and low")
    if (result["low"] > result[["open", "close", "high"]].min(axis=1)).any():
        raise ValueError("low must be at most open, close and high")
    clock = now if now is not None else _now_shanghai()
    clock = pd.Timestamp(clock).tz_convert("Asia/Shanghai")
    if (result["timestamp"].dt.tz_convert("Asia/Shanghai").dt.date > clock.date()).any():
        raise ValueError("future bar dates are not allowed")
    return result.sort_values(["timestamp", "symbol"]).reset_index(drop=True)


def load_bars(path: str | Path) -> pd.DataFrame:
    """Read a daily CSV, preserving six-digit symbols including leading zeros.

    Date-only timestamps mean Shanghai 15:00. Full timestamps must already
    denote that close (naive full timestamps are interpreted in Shanghai).
    """
    bars = pd.read_csv(path, dtype={"symbol": "string"})
    return _validate_bars(bars)


def save_bars(bars: pd.DataFrame, path: str | Path) -> None:
    """Validate and atomically replace a UTF-8 CSV in its destination directory."""
    validated = _validate_bars(bars)
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="", suffix=".tmp",
            prefix=f".{destination.name}.", dir=destination.parent, delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            validated.to_csv(handle, index=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, destination)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _request_date(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"\d{8}|\d{4}-\d{2}-\d{2}", value):
        raise ValueError("date must be YYYYMMDD or YYYY-MM-DD")
    try:
        return pd.Timestamp(value).strftime("%Y%m%d")
    except (ValueError, TypeError) as exc:
        raise ValueError(f"invalid request date: {value}") from exc


def _fetch_one(symbol: str, start: str, end: str, adjustment: int) -> pd.DataFrame:
    params = {
        "secid": f"{'1' if symbol.startswith('5') else '0'}.{symbol}",
        "klt": "101", "fqt": str(adjustment), "beg": start, "end": end,
        "fields1": "f1,f2,f3,f4,f5,f6", "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
    }
    for attempt in range(3):
        try:
            response = requests.get(EASTMONEY_URL, params=params, timeout=20)
            response.raise_for_status()
            break
        except requests.RequestException:
            if attempt == 2:
                raise
    payload = response.json()
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict) or not isinstance(data.get("klines"), list) or not data["klines"]:
        raise ValueError(f"Eastmoney returned no bars for {symbol} (fqt={adjustment})")
    if "code" in data and str(data["code"]) != symbol:
        raise ValueError(f"Eastmoney returned the wrong symbol for {symbol}")
    records = []
    for kline in data["klines"]:
        fields = kline.split(",") if isinstance(kline, str) else []
        if len(fields) < 6:
            raise ValueError(f"malformed Eastmoney kline for {symbol}")
        try:
            records.append({
                "timestamp": fields[0], "symbol": symbol,
                "open": float(fields[1]), "close": float(fields[2]),
                "high": float(fields[3]), "low": float(fields[4]),
                "volume": float(fields[5]) * 100.0,
            })
        except (TypeError, ValueError) as exc:
            raise ValueError(f"non-numeric Eastmoney kline for {symbol}") from exc
    return pd.DataFrame.from_records(records)


def fetch_eastmoney(symbols: list[str], start: str, end: str) -> pd.DataFrame:
    """Fetch complete daily bars and adjusted close features, with no fallback.

    The source reports volume in lots; output volume is shares (lots * 100).
    Today's bar is excluded until 15:10 Shanghai time. Missing symbols,
    mismatched adjusted dates, or inconsistent latest dates fail the whole run.
    Public web data is neither a broker quote nor a guaranteed service.
    """
    if not symbols or len(set(symbols)) != len(symbols):
        raise ValueError("provide a nonempty list of unique symbols")
    if any(not isinstance(symbol, str) or not re.fullmatch(r"\d{6}", symbol) for symbol in symbols):
        raise ValueError("symbols must be six-digit strings")
    begin, finish = _request_date(start), _request_date(end)
    if begin > finish:
        raise ValueError("start date must be no later than end date")
    now = _now_shanghai()
    tables = []
    for symbol in symbols:
        try:
            raw = _validate_bars(_fetch_one(symbol, begin, finish, 0), now=now)
            adjusted = _validate_bars(_fetch_one(symbol, begin, finish, 1), now=now)
            if not raw["timestamp"].equals(adjusted["timestamp"]):
                raise ValueError("raw and adjusted bar dates do not match")
            table = raw.merge(
                adjusted[["timestamp", "symbol", "close"]].rename(columns={"close": "signal_close"}),
                on=["timestamp", "symbol"], how="inner", validate="one_to_one",
            )
            dates = table["timestamp"].dt.tz_convert("Asia/Shanghai")
            table = table.loc[(dates.dt.strftime("%Y%m%d") >= begin) & (dates.dt.strftime("%Y%m%d") <= finish)]
            if now < now.normalize() + pd.Timedelta(hours=15, minutes=10):
                table = table.loc[table["timestamp"].dt.tz_convert("Asia/Shanghai").dt.date < now.date()]
            if table.empty:
                raise ValueError("no completed bars in requested range")
            tables.append(table)
        except (requests.RequestException, ValueError, TypeError, KeyError) as exc:
            raise ValueError(f"market data fetch failed for {symbol}: {exc}") from exc
    latest = {table["timestamp"].max() for table in tables}
    if len(latest) != 1:
        raise ValueError("latest completed bar dates differ across symbols")
    return _validate_bars(pd.concat(tables, ignore_index=True), now=now)


def demo_bars() -> pd.DataFrame:
    """Return deterministic SYNTHETIC data, never historical market evidence.

    Real-looking ETF codes identify test streams only. The 600 business days
    exclude weekends, not exchange holidays. Callers must mark provider=demo.
    """
    rng = np.random.default_rng(20261025)
    dates = pd.bdate_range("2022-01-03", periods=600, tz="Asia/Shanghai") + pd.Timedelta(hours=15)
    timestamps = dates.tz_convert("UTC")
    common = rng.normal(0.0001, 0.009, len(dates))
    records = []
    for index, symbol in enumerate(["510300", "510500", "159915", "588000"]):
        returns = common + rng.normal(0, 0.004 + index * 0.001, len(dates))
        closes = (3.5 + index * 0.7) * np.exp(np.cumsum(returns))
        previous = np.r_[closes[0], closes[:-1]]
        opens = previous * np.exp(rng.normal(0, 0.002, len(dates)))
        highs = np.maximum(opens, closes) * np.exp(rng.uniform(0.001, 0.012, len(dates)))
        lows = np.minimum(opens, closes) * np.exp(-rng.uniform(0.001, 0.012, len(dates)))
        volumes = rng.integers(2_000_000, 12_000_000, len(dates))
        for i, timestamp in enumerate(timestamps):
            records.append({
                "timestamp": timestamp, "symbol": symbol, "open": opens[i],
                "high": highs[i], "low": lows[i], "close": closes[i],
                "volume": int(volumes[i]), "signal_close": closes[i],
            })
    return _validate_bars(pd.DataFrame.from_records(records))


def fetch_tencent(symbols: list[str], start: str, end: str) -> pd.DataFrame:
    """Public Tencent daily bars, chunked to avoid adjusted-history 640-bar cap.

    Provider is selected explicitly in config/manifest, never mixed with other
    feeds inside one snapshot. Raw prices are for accounting, qfq only features.
    """
    begin, finish = pd.Timestamp(_request_date(start)), pd.Timestamp(_request_date(end))
    if not symbols or len(set(symbols)) != len(symbols) or any(not re.fullmatch(r"\d{6}", s) for s in symbols):
        raise ValueError("provide unique six-digit ETF symbols")
    if begin > finish:
        raise ValueError("start date must be no later than end date")
    clock = _now_shanghai()
    tables = []
    endpoint = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    for symbol in symbols:
        code = ("sh" if symbol.startswith("5") else "sz") + symbol
        streams = []
        for adjustment in ("", "qfq"):
            records = []
            cursor = begin
            while cursor <= finish:
                chunk_end = min(finish, cursor + pd.DateOffset(years=2) - pd.Timedelta(days=1))
                params = {"param": f"{code},day,{cursor:%Y-%m-%d},{chunk_end:%Y-%m-%d},640,{adjustment}"}
                for attempt in range(3):
                    try:
                        response = requests.get(endpoint, params=params, timeout=15)
                        response.raise_for_status()
                        break
                    except requests.RequestException:
                        if attempt == 2:
                            raise
                payload = response.json().get("data", {}).get(code, {})
                values = payload.get(adjustment + "day", payload.get("day", []))
                if not isinstance(values, list) or not values:
                    raise ValueError(f"Tencent returned no bars: {symbol}/{cursor.year}/{adjustment}")
                for fields in values:
                    if not isinstance(fields, list) or len(fields) < 6:
                        raise ValueError("malformed Tencent daily bar")
                    day = pd.Timestamp(fields[0])
                    if cursor <= day <= chunk_end:
                        records.append({"timestamp": fields[0], "symbol": symbol,
                                        "open": float(fields[1]), "close": float(fields[2]),
                                        "high": float(fields[3]), "low": float(fields[4]),
                                        "volume": float(fields[5]) * 100})
                cursor = chunk_end + pd.Timedelta(days=1)
            stream = _validate_bars(pd.DataFrame(records), now=clock)
            streams.append(stream)
        raw, adjusted = streams
        if not raw.timestamp.equals(adjusted.timestamp):
            raise ValueError(f"Tencent raw/adjusted dates differ: {symbol}")
        raw["signal_close"] = adjusted.close.to_numpy()
        if clock < clock.normalize() + pd.Timedelta(hours=15, minutes=10):
            raw = raw[raw.timestamp.dt.tz_convert("Asia/Shanghai").dt.date < clock.date()]
        if raw.empty:
            raise ValueError("no completed Tencent daily bars")
        tables.append(raw)
    if len({t.timestamp.max() for t in tables}) != 1:
        raise ValueError("latest completed bar dates differ across Tencent symbols")
    return _validate_bars(pd.concat(tables, ignore_index=True), now=clock)
