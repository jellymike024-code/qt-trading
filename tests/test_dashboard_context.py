import importlib.util
from pathlib import Path

from quantlab.storage import write_json


def load_server():
    path=Path(__file__).resolve().parents[1]/'frontend/serve.py'
    spec=importlib.util.spec_from_file_location('orbit_frontend_server',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_api_never_fills_quotes_or_counts_with_demo_values(tmp_path,monkeypatch):
    server=load_server();monkeypatch.setattr(server,'WORKSPACE',tmp_path)
    data=server.context()
    assert data['quote_mode']=='real_sources_only' and data['markets']=={}
    assert data['bettafish']['status']['status']=='not_connected'
    assert data['bettafish']['status']['comments']==0
    assert data['configuration']=={'initial_cash':None,'risk':None}
    assert not data['equity']


def test_api_excludes_analyses_of_no_longer_active_candidates(tmp_path,monkeypatch):
    server=load_server();monkeypatch.setattr(server,'WORKSPACE',tmp_path)
    write_json(tmp_path/'data/social/candidates.json',{'items':[{'id':'actual-active-record'}]})
    write_json(tmp_path/'reports/social_analysis.json',{'items':[{'record_id':'actual-active-record'}, {'record_id':'old-record'}]})
    write_json(tmp_path/'config.json',{'initial_cash':1000000,'risk':{'max_drawdown_budget':.12},'private_unused_field':'not exposed'})
    data=server.context()
    assert data['social']['analysis']==[{'record_id':'actual-active-record'}]
    assert data['configuration']['initial_cash']==1000000
    assert 'private_unused_field' not in data['configuration']
