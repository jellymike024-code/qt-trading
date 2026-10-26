"""Free public topic/news collection, separate from actual comment sentiment.

No accounts, cookies, browser profiles or paid search parameters are used.
Hotboard update times are NEVER represented as publication timestamps.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import html
import json
import math
import os
from pathlib import Path
import re
import unicodedata
from urllib.parse import urlsplit

import pandas as pd
import requests

from .storage import read_json, write_json

ROOT = Path(__file__).resolve().parent.parent
HOTBOARD_URL = "https://uapis.cn/api/v1/misc/hotboard"
EASTMONEY_URL = "https://np-listapi.eastmoney.com/comm/web/getFastNewsList"
PLATFORMS = {"weibo": "微博热榜", "douyin": "抖音热榜", "xiaohongshu": "小红书热榜", "eastmoney_news": "东方财富财经快讯",
             "zhihu":"知乎热榜", "bilibili":"B站热门", "toutiao":"今日头条热榜"}
HOSTS = {"weibo": ("weibo.com", "weibo.cn"), "douyin": ("douyin.com",),
         "xiaohongshu": ("xiaohongshu.com", "xhslink.com"), "eastmoney_news": ("eastmoney.com",),
         "zhihu":("zhihu.com",),"bilibili":("bilibili.com","b23.tv"),"toutiao":("toutiao.com",)}


def stamp(value):
    result = pd.Timestamp(value)
    if result.tzinfo is None:
        raise ValueError("timestamps must include a timezone")
    return result.tz_convert("UTC")


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def topic_hash(text):
    normalized = ''.join(c.lower() for c in unicodedata.normalize("NFKC", text) if c.isalnum())
    return digest(normalized)


def valid_url(url, platform=None):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("expected a public HTTPS source URL")
    if any(c.isspace() for c in url):
        raise ValueError("URL must not contain whitespace")
    if platform and not any(parsed.hostname == h or parsed.hostname.endswith('.' + h) for h in HOSTS[platform]):
        raise ValueError("source URL does not belong to the declared platform")
    return url


def _record(platform, title, url, observed, updated, *, rank=None, heat=None, published=None, comment_count=None):
    if not isinstance(title, str) or not title.strip() or len(title) > 600:
        raise ValueError("invalid headline")
    valid_url(url, platform)
    observed, updated = stamp(observed), stamp(updated)
    if updated > observed:
        raise ValueError("provider update time is in the future")
    if rank is not None and (isinstance(rank, bool) or not isinstance(rank, int) or rank < 1):
        raise ValueError("rank must be a positive integer")
    if published is not None and stamp(published) > observed:
        raise ValueError("publication timestamp is in the future")
    return {"id": digest(platform + '\n' + url + '\n' + title.strip()),
            "platform": platform, "provider": "eastmoney_public" if platform == "eastmoney_news" else "uapi_public_hotboard",
            "content_kind": "news_headline" if platform == "eastmoney_news" else "hot_topic",
            "title": title.strip(), "source_url": url, "topic_hash": topic_hash(title),
            "published_at": str(stamp(published)) if published is not None else None,
            "provider_updated_at": str(updated), "observed_at": str(observed),
            "rank": rank, "hot_value": str(heat)[:80] if heat is not None else None,
            "comment_count": comment_count, "comment_text_collected": False}


def parse_hotboard(payload, platform, observed):
    if platform not in HOSTS or platform == "eastmoney_news":
        raise ValueError("unsupported social platform")
    if not isinstance(payload, dict) or payload.get("type") != platform or not isinstance(payload.get("list"), list):
        raise ValueError("unexpected hotboard schema/platform")
    updated = stamp(payload["update_time"])
    if updated > stamp(observed):
        raise ValueError("future hotboard snapshot")
    records, errors = [], []
    for i, item in enumerate(payload["list"][:200]):
        try:
            records.append(_record(platform, item["title"], item["url"], observed, updated,
                                   rank=item.get("index", i+1), heat=item.get("hot_value")))
        except (ValueError, TypeError, KeyError) as exc:
            errors.append({"row": i, "error": str(exc)})
    if payload["list"] and not records:
        raise ValueError("all hotboard records failed validation")
    return records, errors


def parse_eastmoney(payload, observed):
    if str(payload.get("code")) != "1" or not isinstance(payload.get("data", {}).get("fastNewsList"), list):
        raise ValueError("unexpected Eastmoney fast-news response")
    records, errors = [], []
    for i, item in enumerate(payload["data"]["fastNewsList"][:100]):
        try:
            code = str(item["code"])
            if not re.fullmatch(r"\d{12,24}", code):
                raise ValueError("invalid news identifier")
            # The source provides showTime in Beijing time, not UTC.
            published = pd.Timestamp(item["showTime"]).tz_localize("Asia/Shanghai")
            count = int(item.get("pinglun_Num", 0))
            if count < 0:
                raise ValueError("negative comment count")
            records.append(_record("eastmoney_news", item["title"], f"https://finance.eastmoney.com/a/{code}.html",
                                   observed, observed, published=published, comment_count=count))
        except (ValueError, TypeError, KeyError) as exc:
            errors.append({"row": i, "error": str(exc)})
    if payload["data"]["fastNewsList"] and not records:
        raise ValueError("all news records failed validation")
    return records, errors


def read_records(path):
    if not Path(path).exists():
        return []
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def append_observations(path, records):
    """Preserve past observations; repeats cannot rewrite first-seen timestamps."""
    path = Path(path)
    existing = read_records(path)
    seen = {(r["id"], r["observed_at"]) for r in existing}
    additions = []
    for record in records:
        key = record["id"], record["observed_at"]
        if key not in seen:
            additions.append(record)
            seen.add(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".jsonl.tmp")
    temporary.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in existing+additions), encoding="utf-8")
    os.replace(temporary, path)
    return len(additions)


def latest_items(records, asof, hours=24):
    asof = stamp(asof)
    first, latest = {}, {}
    for record in records:
        observed = stamp(record["observed_at"])
        if observed > asof:
            continue
        key = record["id"]
        first[key] = min(first.get(key, observed), observed)
        if key not in latest or stamp(latest[key]["observed_at"]) < observed:
            latest[key] = record
    active = []
    for key, record in latest.items():
        # An old story returning to a list is not a newly published event.
        age_from = stamp(record["published_at"]) if record["published_at"] else first[key]
        if asof-age_from > pd.Timedelta(hours=hours):
            continue
        active.append(record | {"first_seen_at": str(first[key])})
    return sorted(active, key=lambda r: (r["platform"], r["rank"] or 1000, r["id"]))


def candidates(records, keywords):
    result = []
    for record in records:
        hits = [word for word in keywords if word.casefold() in record["title"].casefold()]
        if hits:
            result.append(record | {"matched_keywords": hits, "needs_semantic_review": True})
    return result


def collect(root=ROOT, clock=None, get=requests.get):
    root = Path(root)
    fixed_clock = clock is not None
    clock = stamp(clock if clock is not None else pd.Timestamp.now(tz="UTC"))
    config = read_json(root / "social_sources.json")
    social_dir, output = root / "data" / "social", root / "reports"
    social_dir.mkdir(parents=True, exist_ok=True)
    lock = social_dir / ".collect.lock"
    descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        results, records = {}, []
        sources = [(p, HOTBOARD_URL, {"type": p}) for p in config["hotboards"]]
        if config["eastmoney_fast_news"]:
            sources.append(("eastmoney_news", EASTMONEY_URL,
                            {"client": "web", "biz": "web_724", "fastColumn": "102", "pageSize": "50", "sortEnd": "", "req_trace": ""}))
        for platform, endpoint, params in sources:
            try:
                # No login, keys, time-machine search, or subscription endpoints.
                response = get(endpoint, params=params, timeout=12)
                response.raise_for_status()
                payload = response.json()
                observed = clock if fixed_clock else stamp(pd.Timestamp.now(tz="UTC"))
                parsed, errors = parse_eastmoney(payload, observed) if platform == "eastmoney_news" else parse_hotboard(payload, platform, observed)
                updated = max((stamp(r["provider_updated_at"]) for r in parsed), default=clock)
                stale = observed-updated > pd.Timedelta(hours=config["max_source_age_hours"])
                snapshot = social_dir / "snapshots" / (clock.strftime("%Y%m%dT%H%M%S%fZ") + '-' + platform + '.json')
                write_json(snapshot, {"fetched_at": str(observed), "endpoint": endpoint, "params": params, "payload": payload})
                records.extend(parsed)
                results[platform] = {"status": "stale" if stale else "partial" if errors else "ok",
                                     "records": len(parsed), "comment_text_records": 0,
                                     "provider_updated_at": str(updated), "validation_errors": errors,
                                     "snapshot": str(snapshot), "snapshot_sha256": digest(snapshot.read_text(encoding="utf-8"))}
            except (requests.RequestException, ValueError, TypeError, KeyError) as exc:
                results[platform] = {"status": "error", "records": 0, "comment_text_records": 0, "error": str(exc)}
        append_observations(social_dir / "observations.jsonl", records)
        clock = clock if fixed_clock else stamp(pd.Timestamp.now(tz="UTC"))
        all_records = read_records(social_dir / "observations.jsonl")
        active = latest_items(all_records, clock, config["lookback_hours"])
        healthy = {p for p, value in results.items() if value["status"] in ("ok", "partial")}
        selected = candidates([r for r in active if r["platform"] in healthy], config["keywords"])
        status = {"at": str(clock), "status": "ok" if all(v["status"] == "ok" for v in results.values()) else "partial" if healthy else "error",
                  "sources": results, "active_records": len(active), "financial_candidates": len(selected),
                  "actual_comment_texts": 0, "raw_data_path": str(social_dir / "observations.jsonl"),
                  "coverage_gaps": {"guba_comments": "HTTP403_not_connected", "eastmoney_comments": "counts_only_no_comment_text",
                                    "weibo_comments": "hot_topics_only", "xiaohongshu_comments": "hot_topics_only", "douyin_comments": "hot_topics_only"},
                  "trading_effect": "shadow_analysis_only_frozen_paper_unchanged"}
        write_json(social_dir / "status.json", status)
        write_json(social_dir / "candidates.json", {"asof": str(clock), "items": selected})
        write_json(output / "social_status.json", status)
        render_social(output, status, selected)
        return status
    finally:
        os.close(descriptor)
        lock.unlink(missing_ok=True)


def annotate(candidate, root=ROOT):
    """Validate scheduled Agent interpretations without turning titles into facts."""
    root = Path(root)
    items = read_json(root / "data" / "social" / "candidates.json")
    known = {r["id"]: r for r in items["items"]}
    records = read_json(candidate)
    if not isinstance(records, list):
        raise ValueError("expected a list of Agent annotations")
    checked = []
    for record in records:
        source = known.get(record.get("record_id"))
        if not source:
            raise ValueError("annotation must reference a collected financial candidate")
        for key, lower, upper in (("score", -1, 1), ("confidence", 0, 1)):
            value = record.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not lower <= value <= upper:
                raise ValueError("invalid annotation score/confidence")
        analyzed = stamp(record["analyzed_at"])
        if analyzed < stamp(source["observed_at"]) or analyzed > pd.Timestamp.now(tz="UTC"):
            raise ValueError("analysis timestamp must follow observation and cannot be in the future")
        if not isinstance(record.get("reason"), str) or not record["reason"].strip():
            raise ValueError("analysis requires an explanation")
        group = record.get("topic_group", source["topic_hash"])
        if not isinstance(group, str) or not group.strip() or len(group) > 120:
            raise ValueError("invalid topic group")
        urls = record.get("evidence_urls", [])
        if not isinstance(urls, list) or len(urls) > 10:
            raise ValueError("evidence_urls must be a bounded list")
        for url in urls:
            valid_url(url)
        checked.append({"record_id": source["id"], "score": record["score"], "confidence": record["confidence"],
                        "analyzed_at": str(analyzed), "reason": record["reason"], "source_url": source["source_url"],
                        "topic_group": group, "evidence_urls": urls,
                        "mode": "agent_topic_interpretation_not_verified_fact", "agent": str(record.get("agent", "scheduled_research"))})
    analysis = {"asof": str(pd.Timestamp.now(tz="UTC")), "items": checked,
                "distinct_interpreted_topics": len({r["topic_group"] for r in checked}),
                "trading_effect": "shadow_only", "warning": "题目判断不能代表评论样本情绪或未来涨跌概率"}
    write_json(root / "data" / "social" / "analysis" / (pd.Timestamp.now(tz="UTC").strftime("%Y%m%dT%H%M%S%fZ") + '.json'), analysis)
    write_json(root / "reports" / "social_analysis.json", analysis)
    render_social(root / "reports", read_json(root / "data" / "social" / "status.json"), items["items"], checked)
    return analysis


def render_social(output, status, records, annotations=None):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    reviews = {r["record_id"]: r for r in annotations or []}
    source_rows = ''.join(f'<tr><td>{html.escape(PLATFORMS[p])}</td><td>{html.escape(v["status"])}</td><td>{v["records"]}</td><td>0</td></tr>' for p, v in status["sources"].items())
    cards = []
    for record in records:
        analysis = reviews.get(record["id"])
        explanation = '待Agent语义审核' if analysis is None else f'倾向分数 {analysis["score"]:.2f}；置信度 {analysis["confidence"]:.0%}；' + analysis["reason"]
        pub = record["published_at"] or '未知（榜单更新时间不是原帖发布时间）'
        cards.append(f'<article><p class="meta">{html.escape(PLATFORMS[record["platform"]])} · {html.escape(record["content_kind"])} · ID {record["id"][:12]}</p><a href="{html.escape(record["source_url"], quote=True)}" target="_blank" rel="noreferrer noopener">{html.escape(record["title"])}</a><p>{html.escape(explanation)}</p><p class="meta">发布时间：{html.escape(pub)}<br>首次获取：{html.escape(record["first_seen_at"])}</p></article>')
    body = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>免费舆情采集</title><style>body{{background:#101622;color:#e8edf5;font:16px/1.7 system-ui,"Microsoft YaHei",sans-serif;max-width:1100px;margin:32px auto;padding:0 24px}}a{{color:#80c8ff}}.notice,article{{background:#1c2738;padding:16px;margin:15px 0;border-radius:8px}}.meta{{color:#aab9cc;font-size:13px}}table{{width:100%;border-collapse:collapse}}th,td{{text-align:left;border-bottom:1px solid #354053;padding:10px}}</style><h1>免费舆情采集与影子分析</h1><div class="notice">已采集热榜/财经标题；本次完整评论文本为0条。话题热度、新闻报道与真实评论情绪分开统计。当前不修改已冻结模拟盘仓位。</div><p>采集时间 {html.escape(status["at"])} · 状态 {html.escape(status["status"])} · 活跃条目 {status["active_records"]} · 财经候选 {status["financial_candidates"]}</p><table><tr><th>来源</th><th>状态</th><th>条数</th><th>评论文本</th></tr>{source_rows}</table><h2>财经候选与Agent解释</h2>{''.join(cards) or '<p>当前热榜没有匹配的财经候选；不能据此推断市场没有风险。</p>'}<h2>覆盖缺口</h2><p>股吧评论未接通；东方财富仅获取快讯及评论数量；微博/抖音/小红书为聚合热榜，未获取完整评论区。免费第三方源可能延迟或中断，必须回查原始来源。</p></html>'''
    (output / "social_dashboard.html").write_text(body, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Free public hot-topic/news collection")
    parser.add_argument("command", choices=["collect", "status", "annotate"])
    parser.add_argument("--input", type=Path)
    args = parser.parse_args()
    if args.command == "collect":
        status = collect()
        print(json.dumps(status, ensure_ascii=False, indent=2))
        return 1 if status["status"] == "error" else 0
    if args.command == "status":
        print(json.dumps(read_json(ROOT / "data" / "social" / "status.json"), ensure_ascii=False, indent=2))
        return 0
    if args.input is None:
        parser.error("annotate requires --input")
    result = annotate(args.input)
    print(json.dumps({"annotations": len(result["items"]), "trading_effect": "shadow_only"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
