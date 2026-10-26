"""Control-plane checks do not log in or contact social platforms."""
import importlib.util
from pathlib import Path

import pytest


def load(name):
    path=Path(__file__).resolve().parents[1]/'integrations/BettaFish'/f'{name}.py'
    spec=importlib.util.spec_from_file_location('test_local_'+name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_no_automatic_platforms_does_not_start_browser_or_database(monkeypatch):
    module=load('collector');monkeypatch.setattr(module,'settings',lambda:{'automatic_platforms':[]})
    def forbidden(*args,**kwargs):pytest.fail('unverified platform must not start')
    monkeypatch.setattr(module,'_crawl',forbidden)
    assert module.crawl(automatic=True)['status']=='skipped'


def test_concurrent_crawl_is_rejected_before_any_external_action(tmp_path,monkeypatch):
    module=load('collector');monkeypatch.setattr(module,'RUNTIME',tmp_path)
    (tmp_path/'crawl.lock').write_text('123')
    def forbidden(*args,**kwargs):pytest.fail('must not run duplicate browser')
    monkeypatch.setattr(module,'_crawl',forbidden)
    with pytest.raises(FileExistsError):module.crawl(['wb'])
    assert (tmp_path/'crawl.lock').read_text()=='123'


def test_failed_run_releases_only_its_own_lock(tmp_path,monkeypatch):
    module=load('collector');monkeypatch.setattr(module,'RUNTIME',tmp_path)
    def failing(*args,**kwargs):raise RuntimeError('test-only failure')
    monkeypatch.setattr(module,'_crawl',failing)
    with pytest.raises(RuntimeError):module.crawl(['wb'])
    assert not (tmp_path/'crawl.lock').exists()


@pytest.mark.parametrize('platforms,notes',[(['unknown'],10),(['wb'],100),(['xhs'],10)])
def test_invalid_scope_is_rejected_before_database_start(monkeypatch,platforms,notes):
    module=load('collector');monkeypatch.setattr(module,'settings',lambda:{'platforms':['wb','xhs','dy'],'max_notes_per_keyword':20})
    def forbidden(*args,**kwargs):pytest.fail('invalid scope must not touch database')
    monkeypatch.setattr(module,'database_start',forbidden)
    with pytest.raises(ValueError):module._crawl(platforms,notes=notes)


def test_oversized_api_pages_cannot_exceed_per_keyword_post_limit():
    module=load('runner');counts={}
    first=module.bounded_items(list(range(15)),counts,'first-keyword',20)
    second=module.bounded_items(list(range(15,30)),counts,'first-keyword',20)
    assert first+second==list(range(20))
    assert not module.bounded_items([99],counts,'first-keyword',20)
    assert module.bounded_items([88],counts,'other-keyword',20)==[88]


def test_external_video_ids_keep_integer_precision_above_javascript_safe_range():
    module=load('runner')
    assert module.numeric_source_id('7685288512992043624')==7685288512992043624
    assert module.numeric_source_id(7685288512992043624)==7685288512992043624


@pytest.mark.parametrize('value',[True,None,1.5,'1e18','9.9','9223372036854775808','0'])
def test_invalid_ids_cannot_be_silently_rounded_or_truncated(value):
    with pytest.raises(ValueError):load('runner').numeric_source_id(value)


def test_source_get_encoding_matches_browser_and_keeps_empty_parameters():
    query=load('runner').source_query({'image_formats':'jpg,webp,avif','query':'A股 ETF','cursor':'','test_value':'x+y='})
    assert query=='image_formats=jpg,webp,avif&query=A%E8%82%A1%20ETF&cursor=&test_value=x%2By%3D'
