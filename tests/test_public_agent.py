from datetime import datetime, timezone
from copy import deepcopy
import json

from deploy.public_site import public_context, merge_public_context

CLOCK = datetime(2026, 10, 26, 10, 0, tzinfo=timezone.utc)


def example():
    item = {'id':'public-title', 'content_kind':'news_headline','title':'公开市场标题',
        'source_url':'https://finance.eastmoney.com/a/202610261.html',
        'published_at':'2026-10-26T01:00:00Z','observed_at':'2026-10-26T01:01:00Z',
        'first_seen_at':'2026-10-26T01:01:00Z'}
    row = {'record_id':item['id'],'source_url':item['source_url'],
        'reason':'标题未核验，保持待核查，不推导ETF涨跌。','score':0,'confidence':0.3,
        'analyzed_at':'2026-10-26T01:05:00Z','agent':'codex_heartbeat_research',
        'mode':'agent_topic_interpretation_not_verified_fact',
        'evidence_urls':['https://www.sse.com.cn/news/example.html'],
        'private_path':'PRIVATE_CANARY','author':'PRIVATE_CANARY'}
    return {'social':{'status':{'at':'2026-10-26T01:01:00Z'},'items':[item],'analysis':[row]}}


def test_public_agent_preserves_actual_time_and_allowlisted_fields_only():
    source=example()
    data=public_context(source,now=CLOCK)
    row=data['social']['agent_analysis'][0]
    assert row['method']=='daily_research_agent'
    assert row['analyzed_at']=='2026-10-26T01:05:00+00:00'
    assert row['expires_at']=='2026-10-27T01:00:00+00:00'
    assert row['source_title']=='公开市场标题'
    assert 'PRIVATE_CANARY' not in json.dumps(data)
    assert data['social']['status']['daily_agent']['realtime_model_enabled'] is False
    assert public_context(data,now=CLOCK)['social']['agent_analysis']==data['social']['agent_analysis']


def test_public_agent_rejects_comments_mismatches_future_and_expired():
    for change in ('comment','mismatch','future','expired','unlabelled'):
        source=example(); item=source['social']['items'][0]; row=source['social']['analysis'][0]
        if change=='comment':item['content_kind']='comment'
        elif change=='mismatch':row['source_url']='https://finance.eastmoney.com/different'
        elif change=='future':row['analyzed_at']='2026-10-27T01:00:00Z'
        elif change=='expired':item['published_at']='2026-10-24T01:00:00Z'
        else:row.pop('agent')
        row['reason']='PRIVATE_CANARY'
        data=public_context(source,now=CLOCK)
        assert data['social']['agent_analysis']==[], change
        assert 'PRIVATE_CANARY' not in json.dumps(data), change


def test_public_evidence_cannot_link_private_hosts_or_embed_credentials():
    source=example();row=source['social']['analysis'][0]
    row['evidence_urls']=['http://www.sse.com.cn/test','https://localhost/secrets',
        'https://secret@www.sse.com.cn/test','https://www.sse.com.cn.evil.example/test',
        'https://www.sse.com.cn:123/test','https://www.sse.com.cn/valid']
    row['score']=float('nan');row['confidence']=True
    result=public_context(source,now=CLOCK)['social']['agent_analysis'][0]
    assert result['evidence_urls']==['https://www.sse.com.cn/valid']
    assert 'score' not in result and 'confidence' not in result


def test_daily_summary_survives_fresh_collection_but_expires_on_original_clock():
    base=public_context(example(),now=CLOCK)
    fresh=public_context({'social':{'status':{'at':'2026-10-26T10:00:00Z'},'items':[]}},now=CLOCK)
    data=merge_public_context(base,fresh,now=CLOCK)
    assert len(data['social']['agent_analysis'])==1
    assert data['social']['status']['at']=='2026-10-26T10:00:00Z'
    expired=merge_public_context(base,fresh,now=datetime(2026,10,27,10,tzinfo=timezone.utc))
    assert expired['social']['agent_analysis']==[]


def test_repeated_ci_observation_keeps_original_first_seen_and_analysis():
    base=public_context(example(),now=CLOCK)
    source=example(); source['social']['items'][0].update({
        'first_seen_at':'2026-10-26T09:00:00Z','observed_at':'2026-10-26T09:00:00Z'})
    source['social']['analysis']=[]
    fresh=public_context(source,now=CLOCK)
    result=merge_public_context(base,fresh,now=CLOCK)
    assert len(result['social']['agent_analysis'])==1
    assert result['social']['items'][0]['first_seen_at']=='2026-10-26T01:01:00+00:00'
    assert result['social']['items'][0]['observed_at']=='2026-10-26T09:00:00Z'


def test_public_summary_cannot_move_to_another_title_at_the_same_url():
    data=public_context(example(),now=CLOCK)
    data['social']['items'][0]['title']='Changed title'
    assert not public_context(data,now=CLOCK)['social']['agent_analysis']
