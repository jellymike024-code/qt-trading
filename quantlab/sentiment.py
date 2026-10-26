"""Point-in-time news evidence filters; this module does not call an LLM.

JSONL records may be produced by human review or an external agent pipeline.
The evidence must have been published, observed *and* analyzed before a decision.
Positive sentiment never increases an allocation. Missing evidence is reported
explicitly and leaves the price/volume strategy unchanged.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit


MIN_CONFIDENCE = 0.60
MIN_MULTIPLIER = 0.25


def _timestamp(value: Any, field: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"{field} must be an ISO 8601 timestamp") from exc
    else:
        raise ValueError(f"{field} must be a timezone-aware timestamp")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a nonempty string")
    return value.strip()


def _number(value: Any, field: str, lower: float, upper: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a finite number")
    value = float(value)
    if not math.isfinite(value) or not lower <= value <= upper:
        raise ValueError(f"{field} must be between {lower} and {upper}")
    return value


def _normalize_event(record: Any) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise ValueError("each event must be a JSON object")
    required = (
        "id", "symbol", "published_at", "observed_at", "analyzed_at",
        "score", "confidence", "source_url", "evidence",
    )
    missing = [field for field in required if field not in record]
    if missing:
        raise ValueError(f"missing event fields: {', '.join(missing)}")

    event = {field: _text(record[field], field) for field in ("id", "symbol", "source_url", "evidence")}
    if any(char.isspace() for char in event["symbol"]) or ("*" in event["symbol"] and event["symbol"] != "*"):
        raise ValueError("symbol must be one exact identifier or '*'")
    try:
        url = urlsplit(event["source_url"])
        valid_url = (
            url.scheme in {"http", "https"}
            and bool(url.hostname)
            and url.username is None
            and url.password is None
            and not any(char.isspace() for char in event["source_url"])
        )
        # Accessing port also validates malformed numeric ports.
        _ = url.port
    except ValueError as exc:
        raise ValueError("source_url must be a valid http(s) evidence URL") from exc
    if not valid_url:
        raise ValueError("source_url must be a valid http(s) evidence URL")

    timestamps = {field: _timestamp(record[field], field) for field in ("published_at", "observed_at", "analyzed_at")}
    available = max(timestamps.values())
    event.update({field: value.isoformat() for field, value in timestamps.items()})
    event["available_at"] = available.isoformat()
    event["score"] = _number(record["score"], "score", -1.0, 1.0)
    event["confidence"] = _number(record["confidence"], "confidence", 0.0, 1.0)
    if record.get("expires_at") is not None:
        expiry = _timestamp(record["expires_at"], "expires_at")
        if expiry <= available:
            raise ValueError("expires_at must follow available_at")
        event["expires_at"] = expiry.isoformat()
    if record.get("agent") is not None:
        event["agent"] = _text(record["agent"], "agent")
    return event


def load_events(path: str | Path) -> list[dict[str, Any]]:
    """Read and validate JSONL; return UTC ISO strings and derived available_at.

    Duplicate IDs are retained here to preserve timestamped revisions. The
    evaluator selects only the newest revision available at the decision time.
    No URL is fetched and source text is never treated as executable instruction.
    """
    events: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                events.append(_normalize_event(record))
            except (ValueError, TypeError) as exc:
                raise ValueError(f"invalid sentiment event at line {line_number}: {exc}") from exc
    return events


def evaluate_events(
    events: Iterable[dict[str, Any]],
    symbol: str,
    asof: str | datetime,
    max_age_hours: float = 24,
) -> dict[str, Any]:
    """Return a deterministic, long-only allocation multiplier in [0.25, 1].

    Expiration is the earlier of publication + max_age_hours and expires_at.
    Newly analyzing an old headline cannot refresh its age. Timestamp order in
    the input does not matter: availability uses the latest of all three times.
    Duplicate IDs count once, choosing the latest revision already available;
    an expired revision cannot reveal an older superseded revision again.

    Confidence-weighted score is informational. For risk control, the strongest
    individual negative event with confidence >= 0.6 reduces allocation by
    abs(score) * confidence, with a 0.25 floor. Positive evidence cannot cancel
    that individual adverse-event filter or increase exposure above baseline.
    This is a transparent rule, not a trained predictor or validated alpha.
    """
    symbol = _text(symbol, "symbol")
    decision_time = _timestamp(asof, "asof")
    age = _number(max_age_hours, "max_age_hours", 0.0, 876_000.0)
    if age == 0:
        raise ValueError("max_age_hours must be positive")
    latest: dict[str, dict[str, Any]] = {}
    for raw in events:
        event = _normalize_event(raw)
        if event["symbol"] not in {symbol, "*"}:
            continue
        if _timestamp(event["available_at"], "available_at") > decision_time:
            continue
        previous = latest.get(event["id"])
        # Canonical JSON breaks exact-time ties independently of input order.
        rank = (event["available_at"], json.dumps(event, sort_keys=True))
        previous_rank = None if previous is None else (previous["available_at"], json.dumps(previous, sort_keys=True))
        if previous_rank is None or rank > previous_rank:
            latest[event["id"]] = event

    active: list[dict[str, Any]] = []
    for event in latest.values():
        expiry = _timestamp(event["published_at"], "published_at") + timedelta(hours=age)
        if "expires_at" in event:
            expiry = min(expiry, _timestamp(event["expires_at"], "expires_at"))
        if decision_time < expiry:
            active.append(event)
    active.sort(key=lambda item: (item["available_at"], item["id"]))
    if not active:
        return {
            "multiplier": 1.0, "status": "missing", "score": 0.0,
            "event_ids": [], "mode": "evidence_filter", "event_count": 0,
        }

    weight = sum(event["confidence"] for event in active)
    score = sum(event["score"] * event["confidence"] for event in active) / weight if weight else 0.0
    multiplier = min(
        [1.0]
        + [
            max(MIN_MULTIPLIER, 1.0 + event["score"] * event["confidence"])
            for event in active
            if event["score"] < 0 and event["confidence"] >= MIN_CONFIDENCE
        ]
    )
    return {
        "multiplier": multiplier,
        "status": "risk_reduced" if multiplier < 1.0 else "neutral",
        "score": score,
        "event_ids": [event["id"] for event in active],
        "mode": "evidence_filter",
        "event_count": len(active),
    }
