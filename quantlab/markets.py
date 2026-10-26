"""Real public market observations. Missing providers never get synthetic values."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re
from urllib.parse import urlsplit

import pandas as pd
import requests

from .storage import read_json, write_json

ROOT = Path(__file__).resolve().parent.parent


def numeric(value):
    if isinstance(value, bool) or value is None:
        raise ValueError("missing numeric value")
    result = float(str(value).replace(',', ''))
    if not math.isfinite(result):
        raise ValueError("non-finite market value")
    return result


def observed_quote(price, change, source, source_url, observed, *, quote_time=None,
                   session_date=None, currency=None, interval="snapshot", history=None, **extra):
    price, change = numeric(price), numeric(change)
    observed = pd.Timestamp(observed).tz_convert("UTC")
    parsed = urlsplit(str(source_url))
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('invalid market evidence URL')
    if price <= 0 or abs(change) > 1000:
        raise ValueError("invalid market price/change")
    if quote_time is not None:
        timestamp = pd.Timestamp(quote_time)
        if timestamp.tzinfo is None:
            raise ValueError("quote_time must include timezone")
        if timestamp.tz_convert("UTC") > observed + pd.Timedelta(minutes=1):
            raise ValueError("future market timestamp")
        session_date = session_date or str(timestamp.date())
    if session_date is None or pd.Timestamp(session_date).date() > observed.date():
        raise ValueError("invalid/future market date")
    age = (observed.date() - pd.Timestamp(session_date).date()).days
    return {"price": price, "change_pct": change, "source": source, "source_url": source_url,
            "observed_at": str(observed), "quote_time": str(quote_time) if quote_time is not None else None,
            "session_date": session_date, "currency": currency, "interval": interval,
            "status": "stale" if age > 5 else "ok", "history": history or [], **extra}


def parse_tencent(text, keys, observed):
    values = {}
    for key, body in re.findall(r'v_([^=]+)="([^"]*)";', text):
        if key not in keys:
            continue
        fields = body.split('~')
        try:
            if len(fields) < 33:
                continue
            price, previous, change = numeric(fields[3]), numeric(fields[4]), numeric(fields[32])
            if previous <= 0 or abs((price/previous-1)*100-change) > .08:
                continue
            timezone = 'America/New_York' if key.startswith('us') else 'Asia/Shanghai'
            timestamp = pd.Timestamp(datetime.strptime(fields[30], '%Y%m%d%H%M%S') if fields[30].isdigit() else fields[30]).tz_localize(timezone)
            values[key] = observed_quote(price, change, '腾讯证券公开报价', 'https://qt.gtimg.cn/q='+key,
                observed, quote_time=timestamp, currency='USD' if key.startswith('us') else 'CNY',
                previous_close=previous, source_symbol=fields[2])
        except (ValueError, TypeError, IndexError):
            continue
    return values


def parse_sina_daily(payload, symbol, observed):
    data = payload.get('result', {}).get('data')
    if not isinstance(data, list):
        raise ValueError('missing global index bars')
    bars = []
    for row in data:
        close = numeric(row['c'])
        if close <= 0:
            raise ValueError('invalid index close')
        day = pd.Timestamp(row['d']).date()
        if day > pd.Timestamp(observed).date():
            raise ValueError('future global index bar')
        bars.append({'date': str(day), 'close': close})
    bars.sort(key=lambda b:b['date'])
    if len(bars) < 2 or len({b['date'] for b in bars}) != len(bars):
        raise ValueError('insufficient or duplicate index bars')
    return observed_quote(bars[-1]['close'], (bars[-1]['close']/bars[-2]['close']-1)*100,
        '新浪环球指数日线', f'https://gi.finance.sina.com.cn/hq/daily?symbol={symbol}', observed,
        session_date=bars[-1]['date'], interval='1d', history=bars[-30:], currency='index_points')


def parse_naver(payload, code, observed):
    rows = payload.get('datas')
    if not isinstance(rows,list):
        raise ValueError('missing Korean stock quotes')
    row = next((r for r in rows if r.get('itemCode')==code),None)
    if not row:
        raise ValueError('wrong Korean stock code')
    return observed_quote(row['closePrice'], row['fluctuationsRatio'], 'Naver证券公开报价',
        f'https://stock.naver.com/domestic/stock/{code}/price', observed,
        quote_time=row['localTradedAt'], currency=row.get('currencyType',{}).get('code','KRW'),
        market_session=row.get('marketSessionType'), source_symbol=code,
        session_note='来源可能包含盘后交易会话')


def summarize_sector(stocks, codes):
    rows = [stocks.get(code) for code in codes]
    good = [r for r in rows if r and r.get('status')=='ok' and isinstance(r.get('change_pct'),(int,float)) and math.isfinite(r['change_pct'])]
    dates = {r['session_date'] for r in good}
    # Strict completeness: no silent substitution by a partial set of winners.
    complete = bool(codes) and len(good)==len(codes) and len(dates)==1
    return {'change_pct': sum(r['change_pct'] for r in good)/len(good) if complete else None,
            'coverage': len(good), 'sample_size': len(codes), 'session_date': next(iter(dates)) if len(dates)==1 else None,
            'methodology':'manual_watchlist_equal_weight_not_official_sector_index',
            'status':'ok' if complete else 'incomplete_or_mixed_dates'}


def finalize(result, universe, observed):
    for city, market in universe.items():
        region = result['regions'][city]
        for quote in [region['index'], *region['stocks'].values()]:
            if quote:
                age = (observed.date()-pd.Timestamp(quote['session_date']).date()).days
                quote['status'] = 'stale' if age > 5 else 'ok'
        region['sectors'] = {sector['name']: summarize_sector(region['stocks'], [s['code'] for s in sector['stocks']]) for sector in market['sectors']}
    coverage = {city: {'index': bool(r['index']), 'index_fresh': bool(r['index'] and r['index']['status']=='ok'),
        'stocks': len(r['stocks']), 'fresh_stocks':sum(q['status']=='ok' for q in r['stocks'].values()),
        'requested_stocks': sum(len(s['stocks']) for s in universe[city]['sectors']),
        'complete_sector_samples': sum(s['status']=='ok' for s in r['sectors'].values())} for city,r in result['regions'].items()}
    result['coverage'] = coverage
    result['status'] = 'ok' if all(c['index_fresh'] and c['fresh_stocks']==c['requested_stocks'] for c in coverage.values()) else 'partial'
    result['evaluated_at'] = str(observed)
    return result


def merge_verified(result, universe, rows, observed):
    for row in rows:
        city,code = row.get('city'),row.get('code')
        allowed = {s['code'] for sector in universe.get(city,{}).get('sectors',[]) for s in sector['stocks']}
        if code not in allowed:
            result['errors'][f'{city}:{code}'] = 'unknown watchlist symbol'
            continue
        try:
            imported_at = pd.Timestamp(row['observed_at'])
            if imported_at.tzinfo is None or imported_at > observed:
                raise ValueError('invalid/future snapshot import timestamp')
            quote = observed_quote(row['price'],row['change_pct'],row['source'],row['source_url'],imported_at,
                quote_time=row.get('quote_time'),session_date=row.get('session_date'),currency=row.get('currency'),
                import_method='source_page_snapshot')
            existing = result['regions'][city]['stocks'].get(code)
            if not existing or quote['session_date'] >= existing['session_date']:
                result['regions'][city]['stocks'][code] = quote
                result['errors'].pop(f'{city}:{code}',None)
        except (ValueError,TypeError,KeyError) as exc:
            result['errors'][f'{city}:{code}'] = str(exc)


def reconcile(root=ROOT):
    """Revalidate current source snapshots without refetching network data."""
    root=Path(root);directory=root/'data/markets';result=read_json(directory/'latest.json')
    universe=read_json(root/'frontend/dist/data/market_universe.json');now=pd.Timestamp.now(tz='UTC')
    verified=directory/'verified_quotes.json'
    if verified.exists():
        merge_verified(result,universe,read_json(verified),now)
    result=finalize(result,universe,now)
    write_json(directory/'latest.json',result)
    return result


def collect(root=ROOT, get=requests.get):
    root = Path(root)
    universe = read_json(root/'frontend/dist/data/market_universe.json')
    observed = pd.Timestamp.now(tz='UTC')
    directory = root/'data/markets'
    directory.mkdir(parents=True,exist_ok=True)
    requests_used, errors = {}, {}
    regions = {city:{'index':None,'stocks':{},'sectors':{}} for city in universe}
    def snapshot(name,payload):
        path = directory/'snapshots'/(observed.strftime('%Y%m%dT%H%M%S%fZ')+'-'+name+'.json')
        write_json(path,payload)
        requests_used[name]={'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    # Batch China and US stocks using one ordinary public quote request.
    mappings = {'sh000001':('shanghai',None), 'us.IXIC':('newyork',None)}
    for city in ('shanghai','newyork'):
        for sector in universe[city]['sectors']:
            for stock in sector['stocks']:
                code=stock['code']; key=('sh' if code.startswith('6') else 'sz')+code if city=='shanghai' else 'us'+code
                mappings[key]=(city,code)
    try:
        response=get('https://qt.gtimg.cn/q='+','.join(mappings),timeout=15);response.raise_for_status();response.encoding='gbk'
        timestamp=pd.Timestamp.now(tz='UTC')
        parsed=parse_tencent(response.text,mappings,timestamp)
        snapshot('tencent',{'observed_at':str(timestamp),'text':response.text})
        for key,(city,code) in mappings.items():
            if key not in parsed:errors[key]='missing_or_invalid_tencent_quote';continue
            if code is None:regions[city]['index']=parsed[key]
            else:regions[city]['stocks'][code]=parsed[key]
    except (requests.RequestException,ValueError) as exc:errors['tencent']=str(exc)
    for city,symbol in [('london','UKX'),('seoul','KOSPI')]:
        try:
            response=get('https://gi.finance.sina.com.cn/hq/daily',params={'symbol':symbol,'num':'30'},timeout=15);response.raise_for_status()
            payload=response.json();snapshot('sina-'+symbol,payload)
            regions[city]['index']=parse_sina_daily(payload,symbol,pd.Timestamp.now(tz='UTC'))
        except (requests.RequestException,ValueError,KeyError,TypeError) as exc:errors[city+'_index']=str(exc)
    korean_codes={stock['code'] for sector in universe['seoul']['sectors'] for stock in sector['stocks']}
    def korea(code):
        response=get('https://polling.finance.naver.com/api/realtime/domestic/stock/'+code,timeout=12);response.raise_for_status()
        payload=response.json()
        quote=parse_naver(payload,code,pd.Timestamp.now(tz='UTC'))
        return code,quote,payload
    with ThreadPoolExecutor(max_workers=3) as pool:
        tasks={pool.submit(korea,code):code for code in korean_codes}
        for task in as_completed(tasks):
            code=tasks[task]
            try:
                code,quote,payload=task.result();regions['seoul']['stocks'][code]=quote;snapshot('naver-'+code,payload)
            except (requests.RequestException,ValueError,KeyError,TypeError) as exc:errors['seoul:'+code]=str(exc)
    # Optional verified page snapshots support providers whose bulk API is not
    # available. These files must contain real source/time fields, never defaults.
    verified_path=directory/'verified_quotes.json'
    if verified_path.exists():
        merge_verified({'regions':regions,'errors':errors},universe,read_json(verified_path),observed)
    result={'mode':'real_sources_only','collected_at':str(pd.Timestamp.now(tz='UTC')),
            'regions':regions,'errors':errors,'snapshots':requests_used,
            'notes':['公开报价可能延迟；日线按真实交易日期显示，缺失数据不填充。','板块表现为人工观察列表全部样本等权均值，非官方行业指数。']}
    finalize(result,universe,pd.Timestamp.now(tz='UTC'))
    write_json(directory/'latest.json',result)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['collect','reconcile','status']);args=parser.parse_args()
    result=collect() if args.command=='collect' else reconcile() if args.command=='reconcile' else read_json(ROOT/'data/markets/latest.json')
    print(json.dumps({k:result[k] for k in ('mode','collected_at','status','coverage','errors')},ensure_ascii=False,indent=2))
