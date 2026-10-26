import json

import pytest

from deploy.public_site import public_context, build


def test_public_export_cannot_leak_ledger_comments_sessions_or_local_paths():
    canary='PRIVATE_CANARY_DO_NOT_PUBLISH'
    source={'paper':{'signals':canary,'accounts':canary},'configuration':{'initial_cash':canary},
        'social':{'status':{'at':'2026-10-26T00:00:00Z','raw_data_path':canary,'sources':{'weibo':{'status':'ok','snapshot':canary}}},
            'items':[{'id':'headline','title':'Public headline','content_kind':'news_headline','source_url':'https://example.org'},
                     {'content_kind':'comment','title':canary}], 'analysis':[{'reason':canary}]},
        'bettafish':{'collector':{'login_image':canary,'log':canary},'dashboard':{'regions':{'shanghai':{'items':[canary]}}}},
        'markets':{'regions':{'shanghai':{'index':{'price':123,'source_url':'https://example.org','private':canary},'stocks':{},'sectors':{}}},'snapshots':canary},
        'research':{'holdout':{'ensemble':{'total_return':-.1,'private':canary}}}}
    data=public_context(source)
    assert canary not in json.dumps(data)
    assert data['markets']['regions']['shanghai']['index']['price']==123
    assert data['research']['holdout']['ensemble']['total_return']==-.1
    assert len(data['social']['items'])==1
    assert data['publication']['original_comments_published'] is False
    assert data['paper']=={} and data['configuration']=={}


def test_static_build_supports_repository_subpath_and_omits_login_endpoint(tmp_path):
    output=build(tmp_path/'site',public_context({}))
    assert 'data-context-url="./data/context.json"' in (output/'index.html').read_text(encoding='utf-8')
    assert (output/'data/context.json').exists()
    assert not (output/'collector.html').exists()
    assert (output/'research/dashboard.html').exists()
    with pytest.raises(ValueError):build(tmp_path,public_context({}))
