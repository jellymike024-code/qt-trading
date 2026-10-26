"""Native startup-failure checks do not launch browsers or access platform sessions."""
import importlib.util
from pathlib import Path

import pytest


def load(name):
    path=Path(__file__).resolve().parents[1]/'integrations/BettaFish'/f'{name}.py'
    spec=importlib.util.spec_from_file_location('test_failure_'+name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


class TargetClosedError(RuntimeError):
    pass


@pytest.mark.parametrize('code',['3221225477','-1073741819','0xC0000005'])
def test_actual_native_launch_crash_is_distinct_from_login_failure(code):
    runner=load('runner')
    error=TargetClosedError('BrowserType.launch_persistent_context: Target page, context or browser has been closed\nBrowser logs:\n  - <process did exit: exitCode='+code+', signal=null>')
    result=runner.failure_details(error,'collecting')
    assert result['status']=='browser_startup_crash'
    assert result['error_type']=='TargetClosedError'
    assert result['error_code']=='0xC0000005'
    assert result['failure_stage']=='browser_startup'
    assert '尚未验证平台登录' in result['message']


@pytest.mark.parametrize('message',[
    'BrowserType.launch_persistent_context: closed\nexitCode=1',
    'Page.goto: closed\nexitCode=3221225477',
    'BrowserType.launch_persistent_context: closed without an exit code',
])
def test_other_closed_targets_are_not_misreported_as_proven_native_launch_crashes(message):
    result=load('runner').failure_details(TargetClosedError(message),'collecting')
    assert result['status']=='error'
    assert result['error_type']=='TargetClosedError'
    assert 'error_code' not in result


def test_manual_login_and_platform_access_restrictions_keep_their_existing_states():
    runner=load('runner')
    assert runner.failure_details(RuntimeError('login required'),'login_required')['status']=='login_required'
    assert runner.failure_details(runner.SourceRestricted('platform limit'),'collecting')['status']=='source_unavailable'


def test_control_plane_preserves_child_startup_crash_and_existing_database_totals(tmp_path,monkeypatch):
    collector=load('collector')
    monkeypatch.setattr(collector,'RUNTIME',tmp_path)
    monkeypatch.setattr(collector,'CRAWLER',tmp_path)
    monkeypatch.setattr(collector,'STATUS',tmp_path/'status.json')
    monkeypatch.setattr(collector,'settings',lambda:{'platforms':['wb'],'max_notes_per_keyword':20,'platform_timeout_seconds':900})
    for name in ('database_start','seed_topics','sync_dashboard'):
        monkeypatch.setattr(collector,name,lambda:None)
    monkeypatch.setattr(collector,'environment',lambda:{})
    monkeypatch.setattr(collector,'database_env',lambda:{})
    monkeypatch.setattr(collector,'export_database',lambda:{'weibo':42})
    monkeypatch.setattr(collector,'verify_sessions',lambda platforms:None)
    class FailedChild:
        pid=123
        def __init__(self,*args,**kwargs):pass
        def wait(self,timeout):
            status=collector.read(collector.STATUS)
            status['platforms']['wb'].update(status='browser_startup_crash',error_type='TargetClosedError',error_code='0xC0000005',failure_stage='browser_startup')
            collector.write(collector.STATUS,status)
            return 1
    monkeypatch.setattr(collector.subprocess,'Popen',FailedChild)
    result=collector._crawl(['wb'],headless=True)
    platform=result['platforms']['wb']
    assert platform['status']=='browser_startup_crash'
    assert platform['return_code']==1
    assert platform['error_type']=='TargetClosedError'
    assert platform['error_code']=='0xC0000005'
    assert platform['failure_stage']=='browser_startup'
    assert result['database_export']=={'weibo':42}
    assert result['status']=='idle' and result['active_platform'] is None
