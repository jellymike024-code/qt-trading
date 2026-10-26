"""Read-only Tencent dates: python -B -m quantlab.diagnostics tencent.

Responses stay in memory. No bars, reports, locks, settings, or ledger are saved.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

import pandas as pd
import requests

from .data import _validate_bars

ROOT = Path(__file__).resolve().parents[1]
ENDPOINT = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"


def date_chunks(start, end):
    """Mirror the frozen loader's inclusive two-year request windows."""
    if any(not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)
           for value in (start, end)):
        raise ValueError("dates must be YYYY-MM-DD")
    cursor, finish = pd.Timestamp(start), pd.Timestamp(end)
    if cursor > finish:
        raise ValueError("start date must be no later than end date")
    while cursor <= finish:
        last = min(finish, cursor + pd.DateOffset(years=2) - pd.Timedelta(days=1))
        yield cursor.strftime("%Y-%m-%d"), last.strftime("%Y-%m-%d")
        cursor = last + pd.Timedelta(days=1)


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def _quote_time(payload, code):
    quotes = payload.get("qt")
    values = quotes.get(code) if isinstance(quotes, dict) else None
    stamp = values[30] if isinstance(values, list) and len(values) > 30 else None
    if not isinstance(stamp, str) or not re.fullmatch(r"\d{14}", stamp):
        return None
    try:
        return pd.Timestamp(datetime.strptime(stamp, "%Y%m%d%H%M%S")).tz_localize("Asia/Shanghai").isoformat()
    except ValueError:
        return None


def _stream(symbol, begin, end, adjustment, clock, timeout, get, no_cache):
    code = ("sh" if symbol.startswith("5") else "sz") + symbol
    row = {"adjustment": adjustment or "raw", "request_started_at": _utc_now(),
           "response_received_at": None, "http_status": None,
           "provider_http_date": None, "provider_quote_time": None,
           "dates": [], "status": "error"}
    try:
        kwargs = {"params": {"param": f"{code},day,{begin},{end},640,{adjustment}"}, "timeout": timeout}
        if no_cache:
            kwargs["headers"] = {"Cache-Control": "no-cache", "Pragma": "no-cache"}
        response = get(ENDPOINT, **kwargs)
        row["response_received_at"] = _utc_now()
        row["http_status"] = response.status_code
        row["provider_http_date"] = response.headers.get("Date")
        row["cache_headers"] = {key: response.headers[key] for key in
                                ("Age", "Cache-Control", "ETag", "Last-Modified", "Via", "X-Cache")
                                if key in response.headers}
        row["response_bytes"] = len(response.content)
        row["response_sha256"] = hashlib.sha256(response.content).hexdigest()
        response.raise_for_status()
        try:
            body = response.json()
        except ValueError as exc:
            raise ValueError("provider returned invalid JSON") from exc
        data = body.get("data") if isinstance(body, dict) else None
        payload = data.get(code) if isinstance(data, dict) else None
        if not isinstance(payload, dict):
            raise ValueError("provider returned no payload for requested symbol")
        row["provider_quote_time"] = _quote_time(payload, code)
        field = adjustment + "day" if adjustment + "day" in payload else "day"
        row["returned_field"] = field
        values = payload.get(field)
        if not isinstance(values, list) or not values:
            raise ValueError("provider returned no daily bars")
        records, outside = [], 0
        for fields in values:
            if not isinstance(fields, list) or len(fields) < 6:
                raise ValueError("malformed provider daily bar")
            try:
                day = pd.Timestamp(fields[0])
                if day.tzinfo is not None or day != day.normalize():
                    raise ValueError("invalid date")
                if not pd.Timestamp(begin) <= day <= pd.Timestamp(end):
                    outside += 1
                    continue
                records.append({"timestamp": fields[0], "symbol": symbol,
                                "open": float(fields[1]), "close": float(fields[2]),
                                "high": float(fields[3]), "low": float(fields[4]),
                                "volume": float(fields[5]) * 100})
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError("invalid provider daily bar date or numbers") from exc
        row["outside_requested_range"] = outside
        table = _validate_bars(pd.DataFrame(records), now=clock)
        row["dates"] = table.timestamp.dt.tz_convert("Asia/Shanghai").dt.strftime("%Y-%m-%d").tolist()
        row["bars_count"] = len(row["dates"])
        row["first_bar_date"], row["latest_bar_date"] = row["dates"][0], row["dates"][-1]
        row["status"] = "ok"
    except requests.Timeout:
        row["error"] = "network request timed out"
    except requests.HTTPError:
        row["error"] = f"provider HTTP status {row['http_status']}"
    except requests.RequestException as exc:
        # Proxy exceptions can include credentials in URLs. Print only the type.
        row["error"] = f"network request failed ({type(exc).__name__})"
    except (ValueError, TypeError, KeyError) as exc:
        row["error"] = str(exc)
    return row


def diagnose_tencent(symbols, start, end, *, now=None, timeout=15, get=None, no_cache=False):
    if not symbols or len(set(symbols)) != len(symbols) or any(
        not isinstance(symbol, str) or not re.fullmatch(r"\d{6}", symbol) for symbol in symbols
    ):
        raise ValueError("provide unique six-digit ETF symbols")
    if not 0 < timeout <= 120:
        raise ValueError("timeout must be in (0, 120] seconds")
    windows = list(date_chunks(start, end))
    clock = pd.Timestamp(now) if now is not None else pd.Timestamp.now(tz="Asia/Shanghai")
    if clock.tzinfo is None:
        raise ValueError("diagnostic clock must include a timezone")
    clock = clock.tz_convert("Asia/Shanghai")
    get = requests.get if get is None else get
    before = clock < clock.normalize() + pd.Timedelta(hours=15, minutes=10)
    today = clock.strftime("%Y-%m-%d")
    result = {"mode": "read_only_tencent_date_diagnostic", "observed_at": _utc_now(),
              "evaluation_time_shanghai": clock.isoformat(), "endpoint": ENDPOINT,
              "start": start, "end": end, "before_close_buffer": before,
              "request_no_cache": no_cache, "status": "ok", "symbols": {},
              "notes": ["HTTP Date is server response time, not exchange bar time.",
                        "provider_quote_time is a quote snapshot timestamp; historical bars have dates only.",
                        "No source CSV, configuration, ledger, signal, or report is written.",
                        "A completed-date mismatch is an error, including after 15:10; no dates are filled or discarded."]}
    latest = {}
    for symbol in symbols:
        detail = {"chunks": [], "status": "ok"}
        combined = {"raw": set(), "qfq": set()}
        for begin, finish in windows:
            raw = _stream(symbol, begin, finish, "", clock, timeout, get, no_cache)
            qfq = _stream(symbol, begin, finish, "qfq", clock, timeout, get, no_cache)
            chunk = {"start": begin, "end": finish, "raw": raw, "qfq": qfq}
            a, b = set(raw.pop("dates")), set(qfq.pop("dates"))
            if raw["status"] != "ok" or qfq["status"] != "ok":
                chunk["status"] = "source_error"
                detail["status"] = "error"
            else:
                chunk["raw_only_dates"], chunk["qfq_only_dates"] = sorted(a-b), sorted(b-a)
                ca = {date for date in a if not before or date < today}
                cb = {date for date in b if not before or date < today}
                chunk["completed_raw_only_dates"], chunk["completed_qfq_only_dates"] = sorted(ca-cb), sorted(cb-ca)
                if ca != cb:
                    chunk["status"] = "completed_date_mismatch"
                    detail["status"] = "error"
                elif a != b:
                    chunk["status"] = "unclosed_today_mismatch"
                    if detail["status"] == "ok":
                        detail["status"] = "warning"
                else:
                    chunk["status"] = "ok"
                combined["raw"].update(ca)
                combined["qfq"].update(cb)
            detail["chunks"].append(chunk)
        if not combined["raw"] or not combined["qfq"]:
            detail["status"] = "error"
            detail["error"] = "no completed daily bars in requested range"
        elif detail["status"] != "error":
            latest[symbol] = max(combined["raw"])
        detail["latest_completed_raw_date"] = max(combined["raw"], default=None)
        detail["latest_completed_qfq_date"] = max(combined["qfq"], default=None)
        result["symbols"][symbol] = detail
        if detail["status"] == "error":
            result["status"] = "error"
        elif detail["status"] == "warning" and result["status"] == "ok":
            result["status"] = "warning"
    if len(set(latest.values())) > 1:
        result["status"] = "error"
        result["error"] = "latest completed bar dates differ across symbols"
    result["finished_at"] = _utc_now()
    return result


def _local_manifest(root):
    path = root / "data" / "market" / "manifest.json"
    if not path.exists():
        return {"status": "missing"}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return {"status": "available", **{key: value.get(key) for key in
                ("provider", "fetched_at", "latest_session", "sha256")}}
    except (OSError, ValueError, TypeError, AttributeError):
        return {"status": "unreadable", "error": "local manifest could not be read"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("provider", choices=["tencent"])
    parser.add_argument("--symbols", nargs="+", help="default: configured ETF pool")
    parser.add_argument("--start", help="YYYY-MM-DD; default: configured history_start")
    parser.add_argument("--end", help="YYYY-MM-DD; default: today's Shanghai date")
    parser.add_argument("--timeout", type=float, default=15)
    parser.add_argument("--no-cache", action="store_true", help="request cache revalidation; never changes production fetch")
    args = parser.parse_args(argv)
    try:
        config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
        result = diagnose_tencent(args.symbols or config["symbols"], args.start or config["history_start"],
                                  args.end or pd.Timestamp.now(tz="Asia/Shanghai").strftime("%Y-%m-%d"),
                                  timeout=args.timeout, no_cache=args.no_cache)
        result["local_manifest"] = _local_manifest(ROOT)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        result = {"mode": "read_only_tencent_date_diagnostic", "status": "error", "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 1 if result["status"] == "error" else 0


if __name__ == "__main__":
    raise SystemExit(main())