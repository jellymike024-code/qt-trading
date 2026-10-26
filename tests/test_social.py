import copy
import json
from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import pytest
import requests

from quantlab.social import (append_observations, candidates, collect, latest_items,
                             parse_eastmoney, parse_hotboard, read_records, topic_hash, annotate)
from quantlab.storage import write_json


NOW = pd.Timestamp("2026-10-25T14:00:00Z")


def payload(platform="weibo", title="A股ETF话题", updated="2026-10-25T13:00:00Z"):
    hosts = {"weibo": "s.weibo.com", "douyin": "www.douyin.com", "xiaohongshu": "www.xiaohongshu.com"}
    return {"type": platform, "update_time": updated, "list": [
        {"index": 1, "title": title, "url": f'https://{hosts[platform]}/search?q=topic', "hot_value": "1.2w"}]}


def record():
    return parse_hotboard(payload(), "weibo", NOW)[0][0]


def test_hot_topic_is_not_a_comment_and_update_is_not_publication():
    row = record()
    assert row["content_kind"] == "hot_topic"
    assert row["published_at"] is None
    assert not row["comment_text_collected"]
    assert row["provider_updated_at"] != row["observed_at"]


@pytest.mark.parametrize('platform,url',[
    ('zhihu','https://www.zhihu.com/question/123'),
    ('bilibili','https://www.bilibili.com/video/BV123'),
    ('toutiao','https://www.toutiao.com/trending/123/')])
def test_added_free_discovery_sources_remain_topics_not_comments(platform,url):
    p={'type':platform,'update_time':'2026-10-25T13:00:00Z',
       'list':[{'index':1,'title':'A股ETF测试夹具','url':url}]}
    rows,errors=parse_hotboard(p,platform,NOW)
    assert len(rows)==1 and not errors
    assert rows[0]['content_kind']=='hot_topic' and not rows[0]['comment_text_collected']


def test_future_and_wrong_platform_rejected():
    with pytest.raises(ValueError, match="future"):
        parse_hotboard(payload(updated="2026-10-26T00:00:00Z"), "weibo", NOW)
    with pytest.raises(ValueError, match="platform"):
        parse_hotboard(payload(), "douyin", NOW)


def test_untrusted_links_and_bad_records_do_not_look_successful():
    p = payload()
    p["list"][0]["url"] = "https://untrusted.example/"
    with pytest.raises(ValueError, match="all hotboard"):
        parse_hotboard(p, "weibo", NOW)
    p["list"].append(payload()["list"][0])
    rows, errors = parse_hotboard(p, "weibo", NOW)
    assert len(rows) == len(errors) == 1


def test_first_seen_is_not_rewritten_and_future_cannot_contaminate():
    first = record()
    later = first | {"observed_at": str(NOW+pd.Timedelta(hours=2)), "rank": 8}
    current = latest_items([first, later], NOW+pd.Timedelta(hours=1))
    assert len(current) == 1 and current[0]["rank"] == 1
    assert pd.Timestamp(current[0]["first_seen_at"]) == NOW
    refreshed = first | {"observed_at": str(NOW+pd.Timedelta(hours=25))}
    assert not latest_items([first, refreshed], NOW+pd.Timedelta(hours=25))


def test_observation_ingestion_idempotence_and_past_preserved(tmp_path):
    path = tmp_path / "observations.jsonl"
    first = record()
    assert append_observations(path, [first, first]) == 1
    assert append_observations(path, [first]) == 0
    later = first | {"observed_at": str(NOW+pd.Timedelta(hours=2)), "rank": 8}
    assert append_observations(path, [later]) == 1
    assert len(read_records(path)) == 2


def test_keyword_screening_is_candidate_not_sentiment():
    selected = candidates([record()], ["ETF", "股市"])
    assert selected[0]["matched_keywords"] == ["ETF"]
    assert selected[0]["needs_semantic_review"]
    assert "score" not in selected[0]
    assert topic_hash("ETF：上涨！") == topic_hash("etf 上涨")


def test_eastmoney_publication_and_comment_count_are_distinct():
    p = {"code": "1", "data": {"fastNewsList": [{"code": "202610253907261495", "title": "ETF新闻",
         "showTime": "2026-10-25 20:11:38", "pinglun_Num": 27}]}}
    rows, errors = parse_eastmoney(p, NOW)
    assert not errors
    assert pd.Timestamp(rows[0]["published_at"]) == pd.Timestamp("2026-10-25T12:11:38Z")
    assert rows[0]["comment_count"] == 27
    assert not rows[0]["comment_text_collected"]


def test_collection_failure_is_visible_and_no_synthetic_fallback(tmp_path):
    cfg = {"hotboards": ["weibo", "douyin"], "eastmoney_fast_news": False,
           "max_source_age_hours": 6, "lookback_hours": 24, "keywords": ["ETF"]}
    write_json(tmp_path / "social_sources.json", cfg)
    response = Mock()
    response.json.return_value = payload()
    get = Mock(side_effect=[response, requests.ConnectionError("offline")])
    status = collect(tmp_path, NOW, get)
    assert status["status"] == "partial"
    assert status["sources"]["douyin"]["status"] == "error"
    assert status["actual_comment_texts"] == 0
    assert status["financial_candidates"] == 1
    assert len(read_records(tmp_path / "data/social/observations.jsonl")) == 1


def test_stale_feed_does_not_become_actionable_candidate(tmp_path):
    write_json(tmp_path / "social_sources.json", {"hotboards": ["weibo"], "eastmoney_fast_news": False,
               "max_source_age_hours": 6, "lookback_hours": 24, "keywords": ["ETF"]})
    response = Mock()
    response.json.return_value = payload(updated="2026-10-24T00:00:00Z")
    status = collect(tmp_path, NOW, Mock(return_value=response))
    assert status["sources"]["weibo"]["status"] == "stale"
    assert status["financial_candidates"] == 0


def test_no_collection_changes_frozen_paper_or_news(tmp_path):
    write_json(tmp_path / "social_sources.json", {"hotboards": ["weibo"], "eastmoney_fast_news": False,
               "max_source_age_hours": 6, "lookback_hours": 24, "keywords": ["ETF"]})
    frozen = tmp_path / "state/paper.json"
    write_json(frozen, {"frozen": "do not modify"})
    before = frozen.read_bytes()
    response = Mock()
    response.json.return_value = payload()
    collect(tmp_path, NOW, Mock(return_value=response))
    assert frozen.read_bytes() == before
    assert not (tmp_path / "data/news/events.jsonl").exists()


def test_agent_annotation_references_observed_candidate_and_merges_topic(tmp_path):
    write_json(tmp_path / "social_sources.json", {"hotboards": ["weibo"], "eastmoney_fast_news": False,
               "max_source_age_hours": 6, "lookback_hours": 24, "keywords": ["ETF"]})
    response = Mock()
    clock = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=2)
    response.json.return_value = payload(updated=str(clock-pd.Timedelta(hours=1)))
    collect(tmp_path, clock, Mock(return_value=response))
    candidate = json.loads((tmp_path / "data/social/candidates.json").read_text(encoding="utf-8"))["items"][0]
    inbox = tmp_path / "agent.json"
    row = {"record_id": candidate["id"], "score": 0., "confidence": .4,
           "analyzed_at": str(clock+pd.Timedelta(minutes=1)), "reason": "中性测试解释", "topic_group": "one-theme",
           "evidence_urls": ["https://www.sse.com.cn/test"]}
    write_json(inbox, [row])
    result = annotate(inbox, tmp_path)
    assert result["distinct_interpreted_topics"] == 1
    assert result["trading_effect"] == "shadow_only"
    for change in [{"record_id": "unknown"}, {"analyzed_at": str(clock-pd.Timedelta(minutes=1))},
                   {"evidence_urls": ["javascript:alert(1)"]}, {"score": float("inf")}]:
        bad = row | change
        if "score" in change:
            # Input JSON may include a nonstandard Infinity; validator must reject.
            inbox.write_text(json.dumps([bad]), encoding="utf-8")
        else:
            write_json(inbox, [bad])
        with pytest.raises(ValueError):
            annotate(inbox, tmp_path)
