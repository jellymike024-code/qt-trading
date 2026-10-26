"""Protocol fixtures only; never copied into the dashboard's production inbox."""
import json
from pathlib import Path

import pandas as pd
import pytest

from quantlab.bettafish import timestamp, normalize, dashboard, sync, decode_export, annotate
from quantlab.storage import write_json

NOW=pd.Timestamp('2026-10-25T14:00:00Z')
CONFIG=json.loads((Path(__file__).resolve().parents[1]/'bettafish_config.json').read_text(encoding='utf-8'))


def raw(**kwargs):
    return dict(platform='weibo',content_type='comment',comment_id='1',content='A股ETF样本评论',
        publish_time='2026-10-25T20:00:00+08:00',source_url='https://weibo.com/123/abc',user_id='u1',**kwargs)


def record(**changes):
    row=raw();row.update(changes)
    return normalize(row,CONFIG,NOW)


def setup_root(tmp_path):
    write_json(tmp_path/'bettafish_config.json',CONFIG)
    inbox=tmp_path/CONFIG['input_directory'];inbox.mkdir(parents=True)
    return inbox


def test_source_epoch_and_timezone_preserved_without_backdating_import():
    assert timestamp(1792933200000)==pd.Timestamp('2026-10-25T13:00:00Z')
    assert timestamp('2026-10-25 21:00:00')==pd.Timestamp('2026-10-25T13:00:00Z')
    row=record()
    assert row['observed_at_origin']=='adapter_import'
    assert timestamp(row['available_at'])==NOW
    assert row['author_hash']!='u1' and 'shanghai' in row['cities']


@pytest.mark.parametrize('changes',[{'publish_time':None},{'publish_time':'2026-10-26T00:00:00Z'},
    {'source_url':'https://weibo.com.attacker.example/123'},{'source_url':'javascript:alert(1)'},
    {'content_type':'hot_topic'},{'content':''}])
def test_invalid_evidence_not_admitted(changes):
    with pytest.raises(ValueError):record(**changes)


def test_untraceable_and_unscored_are_not_reported_as_neutral():
    rows=[record(),record(comment_id='2',source_url=None,content='无链接A股ETF样本')]
    output=dashboard(rows,[],CONFIG,NOW)
    region=output['regions']['shanghai']
    assert region['comments']==2 and region['deduplicated_sample']==1
    assert region['scored_sample']==0
    assert region['sentiment_pct']==dict(positive=None,neutral=None,negative=None)


def test_text_dedup_author_cap_and_label_denominator():
    rows=[record(comment_id=str(i),content=f'A股ETF样本{i}') for i in range(5)]
    rows.append(record(comment_id='duplicate',content='A股ETF样本0',user_id='other'))
    labels=[dict(record_id=r['id'],analyzed_at=str(NOW),confidence=.8,label='positive') for r in rows]
    region=dashboard(rows,labels,CONFIG,NOW)['regions']['shanghai']
    assert region['comments']==6 and region['deduplicated_sample']==3
    assert region['scored_sample']==3 and region['sentiment_pct']['positive']==100
    labels[0]['analyzed_at']=str(NOW+pd.Timedelta(hours=1))
    assert dashboard(rows,labels,CONFIG,NOW)['regions']['shanghai']['scored_sample']==2


def test_upstream_error_is_distinct_from_empty_result(tmp_path):
    path=tmp_path/'error.json';write_json(path,{'results':[],'error_message':'database unavailable'})
    with pytest.raises(ValueError,match='upstream_query_error'):decode_export(path)
    write_json(path,{'results':[]})
    assert decode_export(path)==[]


def test_import_idempotence_preserves_first_seen_and_records_revision(tmp_path):
    inbox=setup_root(tmp_path);path=inbox/'weibo_comments.json';write_json(path,[raw()])
    assert sync(tmp_path,NOW)['imported']==1
    assert sync(tmp_path,NOW+pd.Timedelta(minutes=10))['imported']==0
    stored=json.loads((tmp_path/'data/bettafish/records.jsonl').read_text(encoding='utf-8'))
    assert timestamp(stored['observed_at'])==NOW
    changed=raw();changed['content']='不同的A股内容';write_json(path,[changed])
    status=sync(tmp_path,NOW+pd.Timedelta(minutes=20))
    assert status['status']=='partial' and status['errors']


def test_missing_crawler_is_explicit_not_connected(tmp_path):
    setup_root(tmp_path)
    result=sync(tmp_path,NOW)
    assert result['status']=='not_connected' and result['comments']==0
    assert result['synthetic_data'] is False


def test_annotations_require_traceable_comment_and_model_provenance(tmp_path):
    inbox=setup_root(tmp_path);write_json(inbox/'weibo_comments.json',[raw()]);sync(tmp_path,NOW)
    row=record();path=tmp_path/'annotation.json'
    label=dict(record_id=row['id'],score=.6,confidence=.9,analyzed_at=str(NOW),reason='test-only model fixture')
    write_json(path,[label])
    with pytest.raises(ValueError,match='provenance'):annotate(path,tmp_path)
    label['model']='test fixture';label['score']=float('nan');path.write_text(json.dumps([label]),encoding='utf-8')
    with pytest.raises(ValueError,match='score'):annotate(path,tmp_path)
    label['score']=.6;write_json(path,[label]);assert annotate(path,tmp_path)==1
