import json

import pytest

from quantlab.news import ingest
from quantlab.sentiment import load_events


def test_validated_ingestion_is_idempotent_and_bad_batch_does_not_modify_log(tmp_path):
    event = {"id": "release-1", "symbol": "*", "published_at": "2026-10-23T16:00:00+08:00",
             "observed_at": "2026-10-23T16:05:00+08:00", "analyzed_at": "2026-10-23T16:06:00+08:00",
             "score": -.5, "confidence": .8, "source_url": "https://www.sse.com.cn/example",
             "evidence": "Synthetic ingestion test only"}
    candidate, dest = tmp_path / "inbox.jsonl", tmp_path / "events.jsonl"
    candidate.write_text(json.dumps(event), encoding="utf-8")
    assert ingest(candidate, dest) == 1
    assert ingest(candidate, dest) == 0
    assert len(load_events(dest)) == 1
    original = dest.read_bytes()
    candidate.write_text('{"id": "bad"}', encoding="utf-8")
    with pytest.raises(ValueError):
        ingest(candidate, dest)
    assert dest.read_bytes() == original
