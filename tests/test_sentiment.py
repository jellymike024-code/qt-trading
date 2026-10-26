from datetime import datetime
import json

import pytest

from quantlab.sentiment import evaluate_events, load_events


def event(**updates):
    result = {
        "id": "headline-1",
        "symbol": "510300",
        "published_at": "2026-10-23T09:00:00+08:00",
        "observed_at": "2026-10-23T09:01:00+08:00",
        "analyzed_at": "2026-10-23T09:03:00+08:00",
        "score": -0.8,
        "confidence": 0.9,
        "source_url": "https://example.com/announcement/1",
        "evidence": "Example evidence; not a real market announcement.",
    }
    result.update(updates)
    return result


def test_evidence_requires_all_three_timestamps_and_ignores_input_order():
    news = event(observed_at="2026-10-23T10:00:00+08:00")
    assert evaluate_events([news], "510300", "2026-10-23T09:59:00+08:00")["status"] == "missing"
    result = evaluate_events([news], "510300", "2026-10-23T10:00:00+08:00")
    assert result["multiplier"] == pytest.approx(0.28)
    assert result["status"] == "risk_reduced"


def test_future_revision_does_not_change_old_decision_and_duplicates_count_once():
    original = event()
    future = event(analyzed_at="2026-10-23T11:00:00+08:00", score=1.0)
    old = evaluate_events([original], "510300", "2026-10-23T10:00:00+08:00")
    combined = evaluate_events([original, original, future], "510300", "2026-10-23T10:00:00+08:00")
    assert combined == old
    assert combined["event_count"] == 1
    assert evaluate_events([future, original], "510300", "2026-10-23T11:00:00+08:00")["multiplier"] == 1


def test_stale_news_cannot_be_refreshed_by_late_analysis():
    news = event(analyzed_at="2026-10-24T10:00:00+08:00")
    assert evaluate_events([news], "510300", "2026-10-24T10:00:00+08:00")["status"] == "missing"


def test_explicit_expiry_and_exact_boundary():
    news = event(expires_at="2026-10-23T11:00:00+08:00")
    assert evaluate_events([news], "510300", "2026-10-23T10:59:00+08:00")["event_count"] == 1
    assert evaluate_events([news], "510300", "2026-10-23T11:00:00+08:00")["status"] == "missing"


def test_expired_revision_does_not_resurrect_original():
    original = event()
    revision = event(analyzed_at="2026-10-23T10:00:00+08:00", expires_at="2026-10-23T10:30:00+08:00")
    assert evaluate_events([original, revision], "510300", "2026-10-23T11:00:00+08:00")["status"] == "missing"


def test_symbol_isolation_and_macro_wildcard():
    assert evaluate_events([event()], "159915", "2026-10-23T10:00:00+08:00")["status"] == "missing"
    assert evaluate_events([event(symbol="*")], "159915", "2026-10-23T10:00:00+08:00")["event_count"] == 1


def test_positive_and_low_confidence_sentiment_cannot_raise_exposure():
    for news in (event(score=1.0), event(confidence=0.59)):
        assert evaluate_events([news], "510300", "2026-10-23T10:00:00+08:00")["multiplier"] == 1.0


def test_positive_evidence_cannot_offset_high_confidence_adverse_event():
    negative = event()
    positive = event(id="positive", score=1.0, confidence=1.0)
    result = evaluate_events([positive, negative], "510300", "2026-10-23T10:00:00+08:00")
    assert result["score"] > 0
    assert result["multiplier"] == pytest.approx(0.28)
    assert evaluate_events([event(score=-1.0, confidence=1.0)], "510300", "2026-10-23T10:00:00+08:00")["multiplier"] == 0.25


@pytest.mark.parametrize("updates", [
    {"source_url": "javascript:alert(1)"},
    {"source_url": "https://"},
    {"source_url": "https://user:secret@example.com/"},
    {"source_url": "https://example.com:bad/"},
    {"score": float("nan")},
    {"score": float("inf")},
    {"score": 1.1},
    {"score": True},
    {"confidence": -0.1},
    {"confidence": "0.8"},
    {"evidence": " "},
    {"symbol": ""},
    {"symbol": "510*"},
    {"published_at": "2026-10-23T09:00:00"},
    {"observed_at": None},
    {"analyzed_at": "invalid"},
    {"expires_at": "2026-10-23T08:00:00+08:00"},
])
def test_invalid_event_fields_fail_closed(updates):
    with pytest.raises(ValueError):
        evaluate_events([event(**updates)], "510300", "2026-10-23T10:00:00+08:00")


def test_asof_requires_timezone_and_positive_age():
    with pytest.raises(ValueError, match="timezone"):
        evaluate_events([], "510300", datetime(2026, 10, 23))
    with pytest.raises(ValueError, match="positive"):
        evaluate_events([], "510300", "2026-10-23T10:00:00+08:00", max_age_hours=0)


def test_jsonl_loader_normalizes_times_and_preserves_revisions(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text("\n" + json.dumps(event()) + "\n" + json.dumps(event(score=0.5)) + "\n", encoding="utf-8")
    records = load_events(path)
    assert len(records) == 2
    assert records[0]["available_at"] == "2026-10-23T01:03:00+00:00"
    assert evaluate_events(records, "510300", "2026-10-23T10:00:00+08:00")["event_count"] == 1


def test_jsonl_error_identifies_line(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text(json.dumps(event()) + "\n{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="line 2"):
        load_events(path)
