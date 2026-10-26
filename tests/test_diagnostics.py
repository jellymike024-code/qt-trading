import json
from pathlib import Path
import shutil
import subprocess

import pandas as pd
import pytest
import requests

from quantlab import diagnostics as diag


def response(symbol, dates, *, adjusted=False, status=200):
    code = ('sh' if symbol.startswith('5') else 'sz') + symbol
    quote = [''] * 31
    quote[30] = '20240103113100'
    payload = {('qfqday' if adjusted else 'day'):
               [[day, '3', '3.1', '3.2', '2.9', '1000'] for day in dates], 'qt': {code: quote}}
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps({'data': {code: payload}}).encode()
    result.headers.update({'Date': 'Wed, 03 Jan 2024 03:32:00 GMT', 'Cache-Control': 'max-age=600'})
    return result


def run_pair(raw, qfq, clock='2024-01-03T11:31:00+08:00', **kwargs):
    responses = iter([response('510300', raw), response('510300', qfq, adjusted=True)])
    return diag.diagnose_tencent(['510300'], '2024-01-01', '2024-01-03', now=clock,
                                get=lambda *args, **kw: next(responses), **kwargs)


def first_chunk(result):
    return result['symbols']['510300']['chunks'][0]


def test_two_year_windows_are_inclusive_and_contiguous():
    assert list(diag.date_chunks('2021-01-01', '2026-10-26')) == [
        ('2021-01-01', '2022-12-31'), ('2023-01-01', '2024-12-31'), ('2025-01-01', '2026-10-26')]
    windows = list(diag.date_chunks('2020-02-29', '2024-03-01'))
    for previous, current in zip(windows, windows[1:]):
        assert pd.Timestamp(previous[1]) + pd.Timedelta(days=1) == pd.Timestamp(current[0])


@pytest.mark.parametrize('raw,qfq', [(['2024-01-02', '2024-01-03'], ['2024-01-02']),
                                    (['2024-01-02'], ['2024-01-02', '2024-01-03'])])
def test_unclosed_tail_is_warning_with_exact_diff(raw, qfq):
    result = run_pair(raw, qfq)
    chunk = first_chunk(result)
    assert result['status'] == 'warning'
    assert chunk['status'] == 'unclosed_today_mismatch'
    assert chunk['raw_only_dates'] == sorted(set(raw) - set(qfq))
    assert chunk['qfq_only_dates'] == sorted(set(qfq) - set(raw))
    assert chunk['completed_raw_only_dates'] == chunk['completed_qfq_only_dates'] == []
    assert 'dates' not in chunk['raw']


@pytest.mark.parametrize('clock,status', [('2024-01-03T15:09:59+08:00', 'warning'),
                                         ('2024-01-03T15:10:00+08:00', 'error'),
                                         ('2024-01-03T18:04:00+08:00', 'error')])
def test_close_buffer_never_hides_a_completed_mismatch(clock, status):
    result = run_pair(['2024-01-02', '2024-01-03'], ['2024-01-02'], clock)
    assert result['status'] == status
    if status == 'error':
        assert first_chunk(result)['completed_raw_only_dates'] == ['2024-01-03']


def test_historical_missing_day_remains_error_before_close():
    result = run_pair(['2024-01-01', '2024-01-02'], ['2024-01-02'])
    assert result['status'] == 'error'
    assert first_chunk(result)['completed_raw_only_dates'] == ['2024-01-01']


def test_only_today_without_completed_history_is_error():
    result = run_pair(['2024-01-03'], ['2024-01-03'])
    assert result['status'] == 'error'
    assert result['symbols']['510300']['error'] == 'no completed daily bars in requested range'


def test_sources_keep_separate_observation_http_quote_and_bar_times():
    result = run_pair(['2024-01-02'], ['2024-01-02'])
    raw = first_chunk(result)['raw']
    assert result['status'] == 'ok'
    assert raw['provider_http_date'] == 'Wed, 03 Jan 2024 03:32:00 GMT'
    assert raw['provider_quote_time'] == '2024-01-03T11:31:00+08:00'
    assert raw['latest_bar_date'] == '2024-01-02'
    assert pd.Timestamp(raw['request_started_at']).tzinfo is not None
    assert pd.Timestamp(raw['response_received_at']).tzinfo is not None
    assert len(raw['response_sha256']) == 64
    assert raw['bars_count'] == 1


def test_across_symbol_completed_dates_must_match():
    responses = iter([response('510300', ['2024-01-01']), response('510300', ['2024-01-01'], adjusted=True),
                      response('159915', ['2024-01-02']), response('159915', ['2024-01-02'], adjusted=True)])
    result = diag.diagnose_tencent(['510300', '159915'], '2024-01-01', '2024-01-03',
                                  now='2024-01-03T18:04:00+08:00', get=lambda *a, **kw: next(responses))
    assert result['status'] == 'error'
    assert result['error'] == 'latest completed bar dates differ across symbols'


def test_all_chunks_are_checked_and_requests_match_frozen_loader():
    calls = []
    def get(url, **kwargs):
        calls.append(kwargs)
        code, interval, begin, end, limit, adj = kwargs['params']['param'].split(',')
        dates = [begin]
        if begin == '2025-01-01' and not adj:
            dates.append('2026-10-26')
        return response('510300', dates, adjusted=bool(adj))
    result = diag.diagnose_tencent(['510300'], '2021-01-01', '2026-10-26',
                                  now='2026-10-26T18:04:00+08:00', get=get, no_cache=True)
    assert len(calls) == 6
    assert all(call['headers']['Cache-Control'] == 'no-cache' for call in calls)
    assert result['status'] == 'error'
    chunks = result['symbols']['510300']['chunks']
    assert [chunk['status'] for chunk in chunks] == ['ok', 'ok', 'completed_date_mismatch']
    assert chunks[-1]['raw_only_dates'] == ['2026-10-26']


def test_network_errors_cannot_leak_proxy_credentials():
    def get(*args, **kwargs):
        raise requests.exceptions.ProxyError('https://user:SECRET@proxy.invalid')
    result = diag.diagnose_tencent(['510300'], '2024-01-01', '2024-01-03',
                                  now='2024-01-03T11:31:00+08:00', get=get)
    assert result['status'] == 'error'
    assert 'SECRET' not in json.dumps(result)
    assert first_chunk(result)['raw']['error'] == 'network request failed (ProxyError)'


@pytest.mark.parametrize('kind', ['http', 'malformed', 'duplicate', 'future'])
def test_invalid_source_data_is_explicit_error(kind):
    if kind == 'http':
        raw = response('510300', ['2024-01-02'], status=503)
    elif kind == 'duplicate':
        raw = response('510300', ['2024-01-02', '2024-01-02'])
    elif kind == 'future':
        raw = response('510300', ['2024-01-04'])
    else:
        raw = requests.Response()
        raw.status_code = 200
        raw._content = b'not json'
    result = diag.diagnose_tencent(['510300'], '2024-01-01', '2024-01-04',
                                  now='2024-01-03T18:00:00+08:00', get=lambda *a, **kw: raw)
    assert result['status'] == 'error'
    assert first_chunk(result)['raw']['error']


def test_cli_reads_manifest_without_writing_ledger_signals_or_source(tmp_path, monkeypatch, capsys):
    (tmp_path / 'data' / 'market').mkdir(parents=True)
    (tmp_path / 'state').mkdir()
    (tmp_path / 'reports').mkdir()
    (tmp_path / 'config.json').write_text(json.dumps({'symbols': ['510300'], 'history_start': '2024-01-01'}))
    (tmp_path / 'data' / 'market' / 'manifest.json').write_text(json.dumps({
        'provider': 'tencent_public', 'fetched_at': '2024-01-02T16:15:00+08:00', 'latest_session': '2024-01-02'}))
    (tmp_path / 'state' / 'paper.json').write_text('PRIVATE_LEDGER_UNCHANGED')
    (tmp_path / 'reports' / 'signals.json').write_text('FROZEN_ORDERS_UNCHANGED')
    before = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()}
    monkeypatch.setattr(diag, 'ROOT', tmp_path)
    monkeypatch.setattr(diag.requests, 'get', lambda *a, **kw: response('510300', ['2024-01-02']))
    assert diag.main(['tencent', '--end', '2024-01-03']) == 0
    output = json.loads(capsys.readouterr().out)
    assert output['local_manifest']['fetched_at'] == '2024-01-02T16:15:00+08:00'
    assert 'PRIVATE_LEDGER' not in json.dumps(output)
    after = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()}
    assert before == after


def test_cli_failure_is_nonzero(tmp_path, monkeypatch, capsys):
    (tmp_path / 'config.json').write_text(json.dumps({'symbols': ['510300'], 'history_start': '2024-01-01'}))
    monkeypatch.setattr(diag, 'ROOT', tmp_path)
    def get(*a, **kw):
        raise requests.Timeout()
    monkeypatch.setattr(diag.requests, 'get', get)
    assert diag.main(['tencent', '--end', '2024-01-03']) == 1
    assert json.loads(capsys.readouterr().out)['status'] == 'error'


def test_daily_guard_uses_shanghai_time_and_exact_buffer():
    shell = shutil.which('pwsh') or shutil.which('powershell')
    if shell is None:
        pytest.skip('PowerShell unavailable')
    script_path = Path(__file__).resolve().parents[1] / 'Run-Daily.ps1'
    quoted = str(script_path).replace("'", "''")
    script = """
$taskTokens = $null
$taskErrors = $null
$taskAst = [System.Management.Automation.Language.Parser]::ParseFile('__PATH__', [ref]$taskTokens, [ref]$taskErrors)
if ($taskErrors.Count) { throw 'Run-Daily.ps1 parse failed' }
$taskFunction = $taskAst.Find({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Test-QuantlabDailyCycleReady' }, $true)
. ([ScriptBlock]::Create($taskFunction.Extent.Text))
@('2024-01-03T07:09:59Z', '2024-01-03T07:10:00Z', '2024-01-03T18:10:00+11:00', '2024-01-03T06:00:00Z') | ForEach-Object { Test-QuantlabDailyCycleReady -Now ([DateTimeOffset]::Parse($_)) } | ConvertTo-Json -Compress
""".replace('__PATH__', quoted)
    result = subprocess.run([shell, '-NoProfile', '-NonInteractive', '-Command', script],
                            capture_output=True, text=True, check=True)
    assert json.loads(result.stdout) == [False, True, True, False]
    source = script_path.read_text(encoding='utf-8')
    guard = source.index('if (-not (Test-QuantlabDailyCycleReady))')
    assert source.index('python -m quantlab.markets collect') < guard
    assert source.index('python -m quantlab.social collect') < guard
    assert source.index('python -m quantlab.bettafish sync') < guard < source.index('python -m quantlab cycle')
    assert 'return' in source[guard:source.index('python -m quantlab cycle')]