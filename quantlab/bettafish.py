"""Independent BettaFish/MindSpider export adapter with explicit evidence states.

Accepts DBResponse.results / native MediaCrawler JSON exports. It never treats
topic headlines, a generated report, or a comment count as comment text. No
upstream browser login, paid models or arbitrary SQL are launched by this module.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re
from urllib.parse import urlsplit

import pandas as pd

from .storage import read_json, write_json

ROOT = Path(__file__).resolve().parent.parent
PLATFORMS = {'xhs':'xiaohongshu','xiaohongshu':'xiaohongshu','weibo':'weibo','douyin':'douyin',
             'bilibili':'bilibili','tieba':'tieba','zhihu':'zhihu','kuaishou':'kuaishou','guba':'guba'}
HOSTS = {'xiaohongshu':('xiaohongshu.com','xhslink.com'), 'weibo':('weibo.com','weibo.cn'),
         'douyin':('douyin.com',),'bilibili':('bilibili.com','b23.tv'),'tieba':('tieba.baidu.com',),
         'zhihu':('zhihu.com',),'kuaishou':('kuaishou.com',),'guba':('eastmoney.com',)}


def timestamp(value, source_timezone='Asia/Shanghai'):
    if value is None or value=='':
        raise ValueError('missing source timestamp')
    if isinstance(value,(int,float)) or isinstance(value,str) and re.fullmatch(r'\d{10,16}',value):
        number=float(value)
        return pd.to_datetime(number,unit='ms' if number>=1e12 else 's',utc=True)
    result=pd.Timestamp(value)
    if result.tzinfo is None:
        result=result.tz_localize(source_timezone)
    return result.tz_convert('UTC')


def first(row,keys,default=None):
    return next((row[k] for k in keys if row.get(k) is not None and row[k]!=''),default)


def fingerprint(text):
    return hashlib.sha256(re.sub(r'\W+','',text.casefold()).encode('utf-8')).hexdigest()


def normalize(row, config, now, *, platform_hint=None, comment_hint=False):
    raw_platform=str(first(row,['platform'],platform_hint or '')).lower()
    platform=PLATFORMS.get(raw_platform)
    if platform is None:
        raise ValueError('unsupported/missing source platform')
    kind=str(first(row,['content_type','content_kind'],'comment' if comment_hint else '')).lower()
    table=str(row.get('source_table',''))
    kind='comment' if kind in ('comment','reply') or 'comment' in table else kind
    if kind not in ('comment','note','post','video','content'):
        raise ValueError('missing content type; titles and counts are not comments')
    content=first(row,['title_or_content','content','content_text','desc'])
    if not isinstance(content,str) or not content.strip() or len(content)>30000:
        raise ValueError('missing/oversized original content text')
    published=timestamp(first(row,['publish_time','create_date_time','create_time','time','created_time','ts']),config['default_source_timezone'])
    observed=timestamp(first(row,['observed_at','first_seen_at','collected_at'],now))
    now=timestamp(now)
    if published>now or observed>now:
        raise ValueError('future source/collection timestamp')
    url=first(row,['source_url','url','comment_url','note_url','video_url','content_url','aweme_url'])
    traceable=False
    if url:
        parsed=urlsplit(str(url)); host=parsed.hostname or ''
        if parsed.scheme not in ('https','http') or parsed.username or parsed.password or not any(host==h or host.endswith('.'+h) for h in HOSTS[platform]):
            raise ValueError('invalid original platform evidence URL')
        traceable=True
    author=first(row,['user_id','author_id','uid','author_nickname','nickname','user_nickname'])
    author_hash=hashlib.sha256((platform+':'+str(author)).encode()).hexdigest() if author else None
    native_id=first(row,['comment_id','id','note_id','aweme_id','content_id'])
    digest=hashlib.sha256((platform+':'+kind+':'+str(native_id or content+str(published)+str(author_hash))).encode()).hexdigest()
    text=content.strip(); search=(text+' '+str(row.get('source_keyword',''))+' '+str(row.get('parent_title',''))).casefold()
    cities=[city for city,aliases in config['city_aliases'].items() if any(alias.casefold() in search for alias in aliases)]
    return {'id':digest,'platform':platform,'content_kind':kind,'title':text[:140], 'text':text,
            'source_url':url,'traceable':traceable,'author_hash':author_hash,'native_id':str(native_id) if native_id is not None else None,
            'published_at':str(published),'observed_at':str(observed),'available_at':str(max(published,observed)),
            'observed_at_origin':'source_record' if any(row.get(k) for k in ['observed_at','first_seen_at','collected_at']) else 'adapter_import',
            'text_hash':fingerprint(text),'cities':cities,'source_keyword':str(row.get('source_keyword','')),
            'provider':'BettaFish/MindSpider export','is_verified_fact':False}


def decode_export(path):
    path=Path(path)
    if path.suffix.lower()=='.jsonl':
        payload=[json.loads(line) for line in path.read_text(encoding='utf-8-sig').splitlines() if line.strip()]
    else:payload=json.loads(path.read_text(encoding='utf-8-sig'))
    if isinstance(payload,dict):
        if payload.get('error_message'):
            raise ValueError('upstream_query_error: '+str(payload['error_message'])[:160])
        records=payload.get('results',payload.get('data'))
    else:records=payload
    if not isinstance(records,list) or not all(isinstance(row,dict) for row in records):
        raise ValueError('expected raw comment/post records or DBResponse.results, not a generated report')
    return records


def load_records(path):
    if not Path(path).exists():return []
    return [json.loads(line) for line in Path(path).read_text(encoding='utf-8').splitlines() if line.strip()]


def sync(root=ROOT, now=None):
    root=Path(root); now=timestamp(now if now is not None else pd.Timestamp.now(tz='UTC'))
    config=read_json(root/'bettafish_config.json'); directory=root/'data/bettafish';directory.mkdir(parents=True,exist_ok=True)
    input_dir=(root/config['input_directory']).resolve()
    files=sorted(input_dir.glob('*.json'))+sorted(input_dir.glob('*.jsonl')) if input_dir.exists() else []
    records=load_records(directory/'records.jsonl'); by_id={r['id']:r for r in records}; imported=0;errors=[]
    for path in files:
        try:
            rows=decode_export(path)
            file_hash=hashlib.sha256(path.read_bytes()).hexdigest()
            hint=next((name for name in PLATFORMS if path.name.lower().startswith(name)),None)
            comment_hint='comment' in path.name.lower()
            for index,row in enumerate(rows):
                try:
                    value=normalize(row,config,now,platform_hint=hint,comment_hint=comment_hint)
                    if value['id'] not in by_id:
                        value['import_file']=path.name;value['import_sha256']=file_hash;by_id[value['id']]=value;imported+=1
                    else:
                        # Do not refresh first-seen or overwrite original evidence.
                        old=by_id[value['id']]
                        if old['text_hash']!=value['text_hash']:
                            errors.append({'file':path.name,'row':index,'error':'source revision requires a new record identifier'})
                except (ValueError,TypeError,KeyError,OverflowError) as exc:errors.append({'file':path.name,'row':index,'error':str(exc)})
        except (OSError,ValueError,TypeError) as exc:errors.append({'file':path.name,'error':str(exc)})
    target=directory/'records.jsonl';temporary=target.with_suffix('.jsonl.tmp');temporary.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in by_id.values()),encoding='utf-8');temporary.replace(target)
    snapshot=dashboard(list(by_id.values()),load_records(directory/'annotations.jsonl'),config,now)
    runtime_path=directory/'collector_status.json'
    runtime=read_json(runtime_path) if runtime_path.exists() else {}
    status={'at':str(now),'status':('not_connected' if not files else 'error' if errors and not by_id else 'partial' if errors else 'connected'),
            'adapter':'BettaFish/MindSpider export-compatible','upstream_runtime':runtime.get('status','not_started_by_adapter'),'files':len(files),'imported':imported,
            'records':len(by_id),'comments':snapshot['counts']['comments'],'posts':snapshot['counts']['posts'],
            'traceable_comments':snapshot['counts']['traceable_comments'],'errors':errors,
            'analysis_mode':'evidence_and_optional_model_annotations','requirements':{'comment_collection':'upstream platform access and crawler/database or exported data','upstream_agents':'configured model/search API and database'},
            'synthetic_data':False,'lookback_hours':config['lookback_hours']}
    write_json(directory/'status.json',status);write_json(directory/'dashboard.json',snapshot)
    write_json(root/'reports/bettafish_status.json',status)
    return status


def dashboard(records, annotations, config, asof):
    asof=timestamp(asof); earliest=asof-pd.Timedelta(hours=config['lookback_hours'])
    active=[r for r in records if timestamp(r['available_at'])<=asof and earliest<=timestamp(r['published_at'])<=asof]
    labels={}
    for label in annotations:
        if timestamp(label['analyzed_at'])<=asof:labels[label['record_id']]=label
    buckets={}
    for city in ['all',*config['city_aliases']]:
        selected=[r for r in active if city=='all' or city in r['cities']]
        comments=[r for r in selected if r['content_kind']=='comment'];seen_text=set();authors=Counter();sample=[]
        for row in sorted(comments,key=lambda r:r['published_at'],reverse=True):
            if not row['traceable'] or row['text_hash'] in seen_text:continue
            author=row['author_hash']
            if author and authors[author]>=config['max_comments_per_author']:continue
            if author:authors[author]+=1
            seen_text.add(row['text_hash']);sample.append(row)
        counts={'positive':0,'neutral':0,'negative':0}; scored=0
        for row in sample:
            label=labels.get(row['id'])
            if not label or label['confidence']<config['annotation_min_confidence']:continue
            scored+=1;counts[label['label']]+=1
        buckets[city]={'comments':len(comments),'traceable_comments':sum(r['traceable'] for r in comments),
                       'deduplicated_sample':len(sample),'known_unique_authors':len(authors),'scored_sample':scored,
                       'sentiment_counts':counts,'sentiment_pct':{k:v/scored*100 if scored else None for k,v in counts.items()},
                       'items':[{k:r[k] for k in ['id','platform','content_kind','title','source_url','published_at','observed_at','available_at','traceable','cities']} for r in selected[-100:]],
                       'sample_ids':[r['id'] for r in sample[:200]],'sentiment_is_model_annotation':True}
    return {'asof':str(asof),'counts':{'comments':sum(r['content_kind']=='comment' for r in active),'posts':sum(r['content_kind']!='comment' for r in active),
            'traceable_comments':sum(r['content_kind']=='comment' and r['traceable'] for r in active)},'regions':buckets,
            'methodology':{'text_duplicates':'exact normalized text','author_cap':config['max_comments_per_author'],'missing_labels':'unscored_not_neutral','sampling':'bounded latest evidence; not representative of all investors'},
            'annotations':[a for a in labels.values() if a['record_id'] in {r['id'] for r in active}]}


def annotate(path, root=ROOT):
    root=Path(root);directory=root/'data/bettafish';records={r['id']:r for r in load_records(directory/'records.jsonl')};now=pd.Timestamp.now(tz='UTC')
    checked=[]
    for row in read_json(path):
        record=records.get(row.get('record_id'))
        if not record or record['content_kind']!='comment' or not record['traceable']:raise ValueError('annotation requires a traceable real comment')
        for key,low,high in [('score',-1,1),('confidence',0,1)]:
            value=row.get(key)
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not low<=value<=high:raise ValueError('invalid score/confidence')
        analyzed=timestamp(row['analyzed_at'])
        if analyzed<timestamp(record['available_at']) or analyzed>now:raise ValueError('invalid analysis availability')
        label='positive' if row['score']>.15 else 'negative' if row['score']<-.15 else 'neutral'
        if not isinstance(row.get('model'),str) or not row['model'].strip():raise ValueError('model provenance is required')
        if not isinstance(row.get('reason'),str) or not row['reason'].strip():raise ValueError('evidence explanation is required')
        checked.append({k:row[k] for k in ['record_id','score','confidence','model','reason']}|{'label':label,'analyzed_at':str(analyzed)})
    existing=load_records(directory/'annotations.jsonl');target=directory/'annotations.jsonl'
    target.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in existing+checked),encoding='utf-8')
    sync(root)
    return len(checked)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['sync','status','annotate']);parser.add_argument('--input',type=Path);args=parser.parse_args()
    if args.command=='annotate':
        if not args.input:parser.error('--input is required')
        print(json.dumps({'added_annotations':annotate(args.input)}))
    else:
        result=sync() if args.command=='sync' else read_json(ROOT/'data/bettafish/status.json')
        print(json.dumps(result,ensure_ascii=False,indent=2))
