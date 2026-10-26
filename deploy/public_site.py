"""Export only allowlisted public data. Never publish the local API response."""
import argparse
from datetime import datetime, timezone, timedelta
import html
import importlib.util
import json
import math
from urllib.parse import urlsplit
from pathlib import Path
import shutil

ROOT=Path(__file__).resolve().parents[1]
QUOTE_FIELDS=('price','change_pct','source','source_url','observed_at','quote_time','session_date','currency','interval','status','history','session_note')
METRIC_FIELDS=('sessions','total_return','max_drawdown','sharpe','annualized_return','fees','fills','turnover','mean_exposure')


def pick(value,keys):
    return {key:value[key] for key in keys if key in value} if isinstance(value,dict) else {}


PUBLIC_KINDS = {'hot_topic', 'news_headline'}
PUBLIC_HOSTS = ('weibo.com', 'weibo.cn', 'douyin.com', 'xiaohongshu.com', 'xhslink.com',
    'zhihu.com', 'bilibili.com', 'b23.tv', 'toutiao.com', 'eastmoney.com')
EVIDENCE_HOSTS = PUBLIC_HOSTS + ('sse.com.cn', 'szse.cn', 'csrc.gov.cn', 'pbc.gov.cn',
    'gov.cn', 'sina.com.cn', 'federalreserve.gov', 'sec.gov', 'nasdaq.com', 'nyse.com',
    'bankofengland.co.uk', 'londonstockexchange.com', 'krx.co.kr')


def public_url(value, hosts=PUBLIC_HOSTS):
    if not isinstance(value, str):
        return None
    try:
        url = urlsplit(value)
        host = url.hostname or ''
        if (url.scheme == 'https' and not url.username and not url.password
                and url.port in (None, 443)
                and any(host == allowed or host.endswith('.' + allowed) for allowed in hosts)):
            return value
    except ValueError:
        pass
    return None


def utc_time(value):
    try:
        stamp = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return stamp.astimezone(timezone.utc) if stamp.tzinfo else None
    except (ValueError, TypeError, OverflowError):
        return None


def public_agent_analysis(social, clock):
    """Only interpretations linked to an active public title; never comment annotations."""
    items = {row.get('id'): row for row in social.get('items', [])
             if row.get('content_kind') in PUBLIC_KINDS}
    result = {}
    for row in [*(social.get('analysis') or []), *(social.get('agent_analysis') or [])]:
        item = items.get(row.get('record_id'))
        if not item or not public_url(item.get('source_url')):
            continue
        if row.get('source_url') != item['source_url']:
            continue
        if row.get('source_title') is not None and row['source_title'] != item.get('title'):
            continue
        if row.get('source_topic_hash') is not None and item.get('topic_hash') is not None and row['source_topic_hash'] != item['topic_hash']:
            continue
        if not (row.get('method') == 'daily_research_agent' or (
                row.get('agent') == 'codex_heartbeat_research' and
                row.get('mode') == 'agent_topic_interpretation_not_verified_fact')):
            continue
        analyzed = utc_time(row.get('analyzed_at'))
        observed = (utc_time(item.get('first_seen_at')) or utc_time(row.get('first_seen_at'))
                    or utc_time(item.get('observed_at')))
        start = (utc_time(item.get('published_at')) or utc_time(item.get('first_seen_at'))
                 or observed)
        expiry = start + timedelta(hours=24) if start else None
        explicit = utc_time(row.get('expires_at'))
        if explicit and expiry:
            expiry = min(expiry, explicit)
        if (not analyzed or not observed or not expiry or not start
                or analyzed > clock or observed > analyzed or start > clock or expiry <= clock):
            continue
        reason = row.get('reason')
        if not isinstance(reason, str) or not reason.strip():
            continue
        public = {'record_id': item['id'], 'method': 'daily_research_agent',
            'reason': reason.strip()[:1200], 'analyzed_at': analyzed.isoformat(),
            'expires_at': expiry.isoformat(), 'source_title': str(item.get('title', ''))[:500],
            'source_url': item['source_url'], 'published_at': item.get('published_at'),
            'first_seen_at': item.get('first_seen_at') or item.get('observed_at'),
            'evidence_urls': [url for url in row.get('evidence_urls', [])
                              if public_url(url, EVIDENCE_HOSTS)][:10]}
        for field, low, high in [('score', -1, 1), ('confidence', 0, 1)]:
            value = row.get(field)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and low <= value <= high:
                public[field] = value
        old = result.get(public['record_id'])
        if not old or public['analyzed_at'] > old['analyzed_at']:
            result[public['record_id']] = public
    return sorted(result.values(), key=lambda row: row['analyzed_at'], reverse=True)[:100]


def merge_public_context(base, fresh, *, now=None):
    """Refresh public feeds while retaining unexpired daily interpretations and their titles."""
    data = dict(base)
    if fresh['markets'].get('collected_at'):
        data['markets'] = fresh['markets']
    if fresh['social']['status'].get('at'):
        old, new = base['social'], fresh['social']
        daily = [*(old.get('agent_analysis') or old.get('analysis') or []),
                 *(new.get('agent_analysis') or new.get('analysis') or [])]
        daily_ids = {row.get('record_id') for row in daily}
        items = {row.get('id'): row for row in old.get('items', []) if row.get('id') in daily_ids}
        for row in new.get('items', []):
            previous = items.get(row.get('id'))
            if previous and all(previous.get(key) == row.get(key) for key in ('title', 'source_url', 'content_kind')):
                first_seen = [utc_time(value) for value in (previous.get('first_seen_at') or previous.get('observed_at'),
                                                           row.get('first_seen_at') or row.get('observed_at'))]
                first_seen = [value for value in first_seen if value is not None]
                if first_seen:
                    row = {**row, 'first_seen_at': min(first_seen).isoformat()}
            items[row.get('id')] = row
        data['social'] = {**new, 'items': list(items.values()), 'analysis': daily, 'agent_analysis': daily}
    return public_context(data, now=now)


def public_context(source, *, now=None):
    """No original comments, identities, collector internals or paper account data."""
    clock = now or datetime.now(timezone.utc)
    markets=source.get('markets') or {}; social=source.get('social') or {}
    status=social.get('status') or {}; research=source.get('research') or {}
    public_markets=pick(markets,('mode','collected_at','status','coverage','notes'))
    public_markets['regions']={}
    for city,region in markets.get('regions',{}).items():
        public_markets['regions'][city]={
            'index':pick(region.get('index'),QUOTE_FIELDS) or None,
            'stocks':{code:pick(quote,QUOTE_FIELDS) for code,quote in region.get('stocks',{}).items()},
            'sectors':{name:pick(data,('change_pct','coverage','session_date','status')) for name,data in region.get('sectors',{}).items()}}
    public_status=pick(status,('at','status','active_records','financial_candidates','actual_comment_texts','trading_effect'))
    public_status['sources']={name:pick(data,('status','records','comment_text_records','provider_updated_at','observed_at')) for name,data in status.get('sources',{}).items()}
    item_fields=('id','platform','content_kind','title','source_url','published_at','observed_at','first_seen_at','matched_keywords')
    items=[pick(row,item_fields) for row in social.get('items',[]) if row.get('content_kind') in ('hot_topic','news_headline')]
    daily = public_agent_analysis(social, clock)
    public_status['daily_agent'] = {'analyzed_records':len(daily),
        'last_analyzed_at':max((row['analyzed_at'] for row in daily),default=None),
        'method':'daily_research_agent','realtime_model_enabled':False}
    return {'served_at':clock.isoformat(),'quote_mode':'real_sources_only',
        'publication':{'mode':'public_snapshot','original_comments_published':False,'paper_ledger_published':False,
            'note':'公开页仅展示真实公开行情、热榜/财经标题及历史研究汇总；不发布原始评论、登录会话或模拟账本。'},
        'markets':public_markets,'social':{'status':public_status,'items':items,'analysis':daily,'agent_analysis':daily},
        'research':pick(research,('provider','latest_session','holdout_start'))|{
            'holdout':{name:pick(row,METRIC_FIELDS) for name,row in (research.get('holdout') or {}).items()}},
        'equity':[pick(row,('timestamp','equity')) for row in source.get('equity',[])],
        'paper':{},'configuration':{},'bettafish':{'status':{'status':'private_data_not_published','comments':0},'dashboard':{},'collector':{}}}


def local_context(root=ROOT):
    spec=importlib.util.spec_from_file_location('orbit_export_server',root/'frontend/serve.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module.context()


def build(output,context):
    output=Path(output).resolve()
    if output==ROOT or output in ROOT.parents or output.is_relative_to(ROOT/'frontend/dist'):
        raise ValueError('Output must be a separate build directory')
    if output.exists() and any(output.iterdir()) and not (output/'.orbit-public-build').exists():
        raise ValueError('Refuse to copy into an unrelated nonempty directory')
    output.mkdir(parents=True,exist_ok=True);(output/'.orbit-public-build').touch()
    dist=ROOT/'frontend/dist'
    for name in ('index.html','styles.css','live-health.css'):
        shutil.copy2(dist/name,output/name)
    for name in ('js','vendor','assets','data'):
        shutil.copytree(dist/name,output/name,dirs_exist_ok=True)
    page=(output/'index.html').read_text(encoding='utf-8').replace('<html lang="zh-CN"','<html lang="zh-CN" data-context-url="./data/context.json"',1)
    (output/'index.html').write_text(page,encoding='utf-8')
    (output/'data/context.json').write_text(json.dumps(context,ensure_ascii=False,allow_nan=False),encoding='utf-8')
    (output/'.nojekyll').touch()
    reports=output/'research';reports.mkdir(exist_ok=True)
    note=html.escape(context['publication']['note'])
    for name,title,payload in [('dashboard.html','历史研究汇总',context['research']),('social_dashboard.html','公开来源覆盖',context['social']['status'])]:
        body=html.escape(json.dumps(payload,ensure_ascii=False,indent=2))
        (reports/name).write_text(f'<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>{title}</title><style>body{{background:#061221;color:#d8eaff;max-width:1000px;margin:40px auto;padding:20px;font:16px system-ui}}pre{{white-space:pre-wrap}}a{{color:#68d4ff}}</style><h1>{title}</h1><p>{note}</p><p>历史模拟不代表未来收益；行情可能延迟，日期以原始来源为准。</p><a href="../">返回大屏</a><pre>{body}</pre></html>',encoding='utf-8')
    return output


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=ROOT/'.site')
    parser.add_argument('--seed',type=Path);parser.add_argument('--write-seed',type=Path)
    parser.add_argument('--merge-public-seed',type=Path)
    args=parser.parse_args()
    seed=args.seed or args.merge_public_seed
    if seed:
        data=json.loads(seed.read_text(encoding='utf-8'))
        if data.get('publication',{}).get('mode')!='public_snapshot':raise ValueError('Not a sanitized public seed')
        # Apply the same allowlist again, so injected/private fields cannot pass through.
        data=public_context(data)
        if args.merge_public_seed:
            fresh=public_context(local_context())
            data=merge_public_context(data,fresh)
    else:data=public_context(local_context())
    if args.write_seed:
        args.write_seed.parent.mkdir(parents=True,exist_ok=True)
        args.write_seed.write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8',newline='\n')
    print(build(args.output,data))
