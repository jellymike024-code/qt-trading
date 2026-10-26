import copy
import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from quantlab import refresh_market as refresh
from quantlab.data import _validate_bars, load_bars
from quantlab.storage import experiment_hash

CLOCK = '2024-01-03T16:15:00+08:00'
SYMBOLS = ['510300', '159915']


def bars(dates):
    rows = []
    for day in dates:
        for i, symbol in enumerate(SYMBOLS):
            rows.append({'timestamp': day, 'symbol': symbol, 'open': 3.0+i,
                         'high': 3.2+i, 'low': 2.9+i, 'close': 3.1+i,
                         'volume': 100000.0, 'signal_close': 2.8+i})
    return _validate_bars(pd.DataFrame(rows), now=pd.Timestamp('2024-02-01T16:00:00+08:00'))


@pytest.fixture
def case(tmp_path):
    root = tmp_path / 'project'
    for directory in ['quantlab', 'state', 'data/market']:
        (root / directory).mkdir(parents=True, exist_ok=True)
    for name in ('engine.py', 'strategies.py', 'sentiment.py', 'paper.py', 'data.py'):
        (root / 'quantlab' / name).write_bytes(('frozen fixture ' + name).encode())
    config = {'initial_cash': 1000000, 'data_provider': 'tencent_public', 'symbols': SYMBOLS,
              'history_start': '2024-01-01',
              'risk': {'soft_drawdown': .06, 'hard_drawdown': .1, 'max_drawdown_budget': .12,
                       'max_gross_weight': .6, 'max_symbol_weight': .25},
              'execution': {'lot_size': 100, 'commission_rate': .0001, 'min_commission': 5,
                            'slippage_bps': 5, 'limit_pct': {'510300': .1, '159915': .2}}}
    (root / 'config.json').write_text(json.dumps(config), encoding='utf-8')
    old = bars(['2024-01-01', '2024-01-02'])
    old_bytes = old.to_csv(index=False).encode()
    (root / 'data/market/bars.csv').write_bytes(old_bytes)
    manifest = {'provider': 'tencent_public', 'latest_session': str(old.timestamp.max()),
                'rows': len(old), 'sha256': hashlib.sha256(old_bytes).hexdigest()}
    (root / 'data/market/manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    paper = {'fingerprint': experiment_hash(root, config), 'last_session': str(old.timestamp.max()),
             'accounts': {'sentinel': {'pending': {'510300': .125},
                                        'pending_generated_at': '2024-01-02T16:15:00+08:00'}}}
    (root / 'state/paper.json').write_text(json.dumps(paper), encoding='utf-8')
    return root, old, bars(['2024-01-03'])


def with_overlap(old, tail):
    return pd.concat([old.loc[old.timestamp == old.timestamp.max()], tail], ignore_index=True)


def fetch_queue(old, tail):
    responses = iter([old, with_overlap(old, tail)])
    calls = []
    def fetch(symbols, start, end):
        calls.append((list(symbols), start, end))
        return next(responses).copy(deep=True)
    fetch.calls = calls
    return fetch


def tree(root):
    return {path.relative_to(root): path.read_bytes() for path in root.rglob('*') if path.is_file()}


def run(case, **kwargs):
    root, old, tail = case
    return refresh.refresh_market(root, now=CLOCK, fetcher=fetch_queue(old, tail), **kwargs)


def test_default_check_writes_nothing_and_calls_frozen_loader_in_two_ranges(case):
    root, old, tail = case
    before = tree(root)
    fetcher = fetch_queue(old, tail)
    result = refresh.refresh_market(root, now=CLOCK, fetcher=fetcher)
    assert result['status'] == 'ready' and result['applied'] is False
    assert result['old_rows'] == 4 and result['new_rows'] == 2
    assert result['new_sessions'] == 1
    assert fetcher.calls == [(SYMBOLS, '2024-01-01', '2024-01-02'),
                             (SYMBOLS, '2024-01-02', '2024-01-03')]
    assert tree(root) == before


def test_apply_preserves_old_bytes_and_paper_and_records_compatibility_and_recovery(case):
    root, old, tail = case
    before = tree(root)
    result = run(case, apply=True)
    assert result['status'] == 'applied'
    assert (root / 'data/market/bars.csv').read_bytes().startswith(before[Path('data/market/bars.csv')])
    assert (root / 'state/paper.json').read_bytes() == before[Path('state/paper.json')]
    assert (root / 'config.json').read_bytes() == before[Path('config.json')]
    updated = json.loads((root / 'data/market/manifest.json').read_text())
    assert updated['ingestion_adapter'] == 'verified_same_source_append'
    assert updated['sha256'] == hashlib.sha256((root / 'data/market/bars.csv').read_bytes()).hexdigest()
    compat = updated['ingestion_compatibility']
    assert compat['old_history_bytes_preserved']
    assert compat['history_parity']['volume_exact'] and compat['history_parity']['historical_keys_exact']
    assert compat['old_data_sha256'] == hashlib.sha256(before[Path('data/market/bars.csv')]).hexdigest()
    assert compat['paper_before_sha256'] == hashlib.sha256(before[Path('state/paper.json')]).hexdigest()
    backup = Path(result['recovery'])
    assert (backup / 'bars.csv').read_bytes() == before[Path('data/market/bars.csv')]
    assert (backup / 'manifest.json').read_bytes() == before[Path('data/market/manifest.json')]
    assert Path(result['snapshot']).read_bytes() == (root / 'data/market/bars.csv').read_bytes()
    loaded = load_bars(root / 'data/market/bars.csv')
    pd.testing.assert_frame_equal(loaded.loc[loaded.timestamp <= old.timestamp.max()].reset_index(drop=True), old, check_dtype=False)
    assert len(loaded) == 6
    assert not (root / 'state/.cycle.lock').exists()
    assert not list((root / 'data/market').glob('*.tmp'))


@pytest.mark.parametrize('field', refresh.PRICE_FIELDS + ['volume'])
def test_any_historical_price_or_volume_revision_blocks_all_writes(case, field):
    root, old, tail = case
    fetched = old.copy(deep=True)
    fetched.loc[0, field] += .01
    before = tree(root)
    with pytest.raises(refresh.RefreshError, match=f'historical {field} changed'):
        refresh.refresh_market(root, now=CLOCK, fetcher=fetch_queue(fetched, tail), apply=True)
    assert tree(root) == before


def test_only_csv_price_roundtrip_tolerance_is_allowed_but_history_stays_original(case):
    root, old, tail = case
    fetched = old.copy(deep=True)
    fetched.loc[0, 'signal_close'] += 5e-13
    before = tree(root)
    result = refresh.refresh_market(root, now=CLOCK, fetcher=fetch_queue(fetched, tail), apply=True)
    assert result['history_parity']['maximum_price_absolute_differences']['signal_close'] > 0
    assert (root / 'data/market/bars.csv').read_bytes().startswith(before[Path('data/market/bars.csv')])


@pytest.mark.parametrize('change', ['missing', 'added'])
def test_historical_key_loss_or_backfill_is_not_hidden_by_common_date_comparison(case, change):
    root, old, tail = case
    if change == 'missing':
        fetched = old.iloc[1:].copy()
    else:
        fetched = pd.concat([bars(['2023-12-29']), old], ignore_index=True)
    before = tree(root)
    with pytest.raises(refresh.RefreshError, match='historical keys changed'):
        refresh.refresh_market(root, now=CLOCK, fetcher=fetch_queue(fetched, tail), apply=True)
    assert tree(root) == before


@pytest.mark.parametrize('problem', ['sha', 'provider', 'fingerprint', 'manifest_rows', 'paper_date'])
def test_preconditions_fail_before_any_network_request(case, problem):
    root, old, tail = case
    if problem == 'sha':
        (root / 'data/market/bars.csv').write_bytes(b'changed')
    elif problem in ['provider', 'manifest_rows']:
        path = root / 'data/market/manifest.json'
        value = json.loads(path.read_text())
        value['provider' if problem == 'provider' else 'rows'] = 'eastmoney_public' if problem == 'provider' else 999
        path.write_text(json.dumps(value))
    else:
        path = root / 'state/paper.json'
        value = json.loads(path.read_text())
        value['fingerprint' if problem == 'fingerprint' else 'last_session'] = 'changed' if problem == 'fingerprint' else '2024-01-01T07:00:00Z'
        path.write_text(json.dumps(value))
    def forbidden(*a, **kw):
        raise AssertionError('network called before input preconditions')
    before = tree(root)
    with pytest.raises(refresh.RefreshError):
        refresh.refresh_market(root, now=CLOCK, fetcher=forbidden, apply=True)
    assert tree(root) == before


def test_before_1510_refused_without_any_files_or_requests(case):
    root, old, tail = case
    before = tree(root)
    with pytest.raises(refresh.RefreshError, match='15:10'):
        refresh.refresh_market(root, now='2024-01-03T07:09:59Z', fetcher=lambda *a: pytest.fail('network called'))
    assert tree(root) == before
    assert run(case)['status'] == 'ready'


def test_existing_lock_is_preserved_and_prevents_network(case):
    root, old, tail = case
    (root / 'state/.cycle.lock').write_text('12345')
    before = tree(root)
    with pytest.raises(refresh.RefreshError, match='lock already exists'):
        run(case)
    assert tree(root) == before


@pytest.mark.parametrize('problem', ['overlap', 'missing_symbol', 'missing_date', 'future', 'duplicate'])
def test_new_tail_integrity_blocks_promotion(case, problem):
    root, old, tail = case
    clock = CLOCK
    if problem == 'overlap':
        tail = bars(['2024-01-02', '2024-01-03'])
    elif problem == 'missing_symbol':
        tail = tail.iloc[:1].copy()
    elif problem == 'missing_date':
        tail = bars(['2024-01-03', '2024-01-04']).iloc[:-1].copy()
        clock = '2024-01-04T16:15:00+08:00'
    elif problem == 'future':
        tail = bars(['2024-01-04'])
    else:
        tail = pd.concat([tail, tail.iloc[:1]], ignore_index=True)
    before = tree(root)
    with pytest.raises(refresh.RefreshError):
        refresh.refresh_market(root, now=clock, fetcher=fetch_queue(old, tail), apply=True)
    assert tree(root) == before


def test_raw_adjusted_source_failure_never_promotes_partial_input(case):
    root, old, tail = case
    count = 0
    def fetch(*args):
        nonlocal count
        count += 1
        if count == 1:
            return old
        raise ValueError('Tencent raw/adjusted dates differ: 510300')
    before = tree(root)
    with pytest.raises(ValueError, match='raw/adjusted dates differ'):
        refresh.refresh_market(root, now=CLOCK, fetcher=fetch, apply=True)
    assert tree(root) == before


def test_changed_input_during_fetch_is_detected_before_market_promotion(case):
    root, old, tail = case
    original_bars = (root / 'data/market/bars.csv').read_bytes()
    original_manifest = (root / 'data/market/manifest.json').read_bytes()
    calls = 0
    def fetch(*args):
        nonlocal calls
        calls += 1
        if calls == 1:
            return old
        paper = root / 'state/paper.json'
        paper.write_bytes(paper.read_bytes() + b' ')
        return with_overlap(old, tail)
    with pytest.raises(refresh.RefreshError, match='changed during verification'):
        refresh.refresh_market(root, now=CLOCK, fetcher=fetch, apply=True)
    assert (root / 'data/market/bars.csv').read_bytes() == original_bars
    assert (root / 'data/market/manifest.json').read_bytes() == original_manifest
    assert not (root / 'state/.cycle.lock').exists()


def test_same_latest_date_is_noop_after_full_parity_check(case):
    root, old, tail = case
    before = tree(root)
    fetcher = fetch_queue(old, tail.iloc[:0])
    result = refresh.refresh_market(root, now='2024-01-02T16:15:00+08:00', fetcher=fetcher, apply=True)
    assert result['status'] == 'no_new_date' and result['applied'] is False
    assert len(fetcher.calls) == 2
    assert result['source_verified'] is True
    assert tree(root) == before


def test_second_file_replace_failure_restores_original_pair_and_keeps_recovery(case, monkeypatch):
    root, old, tail = case
    before = tree(root)
    replace = refresh.os.replace
    def fail_manifest(source, destination):
        if Path(source).name.startswith('.append-manifest-'):
            raise OSError('simulated manifest rename failure')
        return replace(source, destination)
    monkeypatch.setattr(refresh.os, 'replace', fail_manifest)
    with pytest.raises(refresh.RefreshError, match='original input pair restored or unchanged'):
        run(case, apply=True)
    for relative in ['data/market/bars.csv', 'data/market/manifest.json', 'state/paper.json', 'config.json']:
        assert (root / relative).read_bytes() == before[Path(relative)]
    backups = list((root / 'data/market/recovery').iterdir())
    assert len(backups) == 1
    assert (backups[0] / 'bars.csv').read_bytes() == before[Path('data/market/bars.csv')]
    assert not list((root / 'data/market').glob('*.tmp'))
    assert not (root / 'state/.cycle.lock').exists()


def test_cli_is_check_by_default_and_apply_is_explicit(monkeypatch, capsys):
    called = []
    def fake(root, **kwargs):
        called.append(kwargs)
        return {'status': 'ready', 'applied': kwargs['apply']}
    monkeypatch.setattr(refresh, 'refresh_market', fake)
    assert refresh.main([]) == 0
    assert refresh.main(['--apply']) == 0
    assert called == [{'apply': False}, {'apply': True}]


def test_cli_network_exception_does_not_echo_proxy_secret(monkeypatch, capsys):
    import requests
    def fail(*a, **kw):
        raise requests.exceptions.ProxyError('https://user:SECRET@proxy.invalid')
    monkeypatch.setattr(refresh, 'refresh_market', fail)
    assert refresh.main([]) == 1
    output = capsys.readouterr().out
    assert 'SECRET' not in output
    assert json.loads(output)['applied'] is False

def test_stage_flush_failure_cleans_up_and_preserves_market_pair(case, monkeypatch):
    root, old, tail = case
    before = tree(root)
    def fail(*args):
        raise OSError('simulated storage flush failure')
    monkeypatch.setattr(refresh.os, 'fsync', fail)
    with pytest.raises(OSError, match='storage flush failure'):
        run(case, apply=True)
    assert tree(root) == before
    assert not list((root / 'data/market').glob('*.tmp'))


def test_frozen_code_change_during_fetch_is_refused(case):
    root, old, tail = case
    original_bars = (root / 'data/market/bars.csv').read_bytes()
    original_manifest = (root / 'data/market/manifest.json').read_bytes()
    count = 0
    def fetch(*args):
        nonlocal count
        count += 1
        if count == 1:
            return old
        (root / 'quantlab/engine.py').write_bytes(b'external source change')
        return with_overlap(old, tail)
    with pytest.raises(refresh.RefreshError, match='frozen strategy/data code changed'):
        refresh.refresh_market(root, now=CLOCK, fetcher=fetch, apply=True)
    assert (root / 'data/market/bars.csv').read_bytes() == original_bars
    assert (root / 'data/market/manifest.json').read_bytes() == original_manifest
    assert not (root / 'state/.cycle.lock').exists()

@pytest.mark.parametrize('clock', ['2024-01-06T16:15:00+08:00', '2024-01-08T16:15:00+08:00'])
def test_verified_cutoff_without_new_dates_is_noop_on_calendar_gaps(case, clock):
    root, old, tail = case
    old = old.assign(timestamp=old.timestamp + pd.Timedelta(days=3))
    old_bytes = old.to_csv(index=False).encode()
    (root / 'data/market/bars.csv').write_bytes(old_bytes)
    path = root / 'data/market/manifest.json'
    manifest = json.loads(path.read_text())
    manifest.update(latest_session=str(old.timestamp.max()), sha256=hashlib.sha256(old_bytes).hexdigest())
    path.write_text(json.dumps(manifest))
    path = root / 'state/paper.json'
    paper = json.loads(path.read_text())
    paper['last_session'] = str(old.timestamp.max())
    path.write_text(json.dumps(paper))
    before = tree(root)
    fetcher = fetch_queue(old, tail.iloc[:0])
    result = refresh.refresh_market(root, now=clock, fetcher=fetcher, apply=True)
    assert result['status'] == 'no_new_date'
    assert result['source_verified'] is True and result['new_rows'] == 0
    assert result['tail_overlap_parity']['rows_compared'] == 2
    assert fetcher.calls[1][1:] == ('2024-01-05', pd.Timestamp(clock).strftime('%Y-%m-%d'))
    assert tree(root) == before


@pytest.mark.parametrize('problem', ['missing_all', 'missing_symbol', 'close_revision',
                                     'volume_revision', 'earlier_date', 'empty_source'])
def test_tail_requires_exact_known_cutoff_not_an_assumed_market_closure(case, problem):
    root, old, tail = case
    overlapping = with_overlap(old, tail)
    cutoff = old.timestamp.max()
    if problem == 'missing_all':
        overlapping = tail
    elif problem == 'missing_symbol':
        overlapping = overlapping.drop(overlapping.index[0])
    elif problem == 'close_revision':
        overlapping.loc[0, 'close'] += .01
    elif problem == 'volume_revision':
        overlapping.loc[0, 'volume'] += 1
    elif problem == 'earlier_date':
        overlapping = pd.concat([old.loc[old.timestamp < cutoff], overlapping], ignore_index=True)
    else:
        overlapping = overlapping.iloc[:0]
    responses = iter([old, overlapping])
    before = tree(root)
    with pytest.raises(refresh.RefreshError):
        refresh.refresh_market(root, now=CLOCK, fetcher=lambda *a: next(responses), apply=True)
    assert tree(root) == before