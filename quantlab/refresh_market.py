"""Verify and optionally append same-source market bars without advancing paper.

Default: python -B -m quantlab.refresh_market
Apply:   python -B -m quantlab.refresh_market --apply

The frozen loader is called separately for the entire old history and new tail.
Historical key/value incompatibility is an error, never silently reconciled.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import uuid

import numpy as np
import pandas as pd
import requests

from .data import BAR_COLUMNS, _validate_bars, fetch_tencent, load_bars
from .storage import experiment_hash, validate_config

ROOT = Path(__file__).resolve().parents[1]
ADAPTER = 'verified_same_source_append'
PRICE_FIELDS = ['open', 'high', 'low', 'close', 'signal_close']
COLUMNS = BAR_COLUMNS + ['signal_close']
PRICE_ABSOLUTE_TOLERANCE = 1e-12


class RefreshError(ValueError):
    pass


def _hash(value):
    return hashlib.sha256(value).hexdigest()


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8')


def _read_json_bytes(value, label):
    try:
        result = json.loads(value.decode('utf-8'))
    except (UnicodeError, ValueError) as exc:
        raise RefreshError(f'{label} is not valid UTF-8 JSON') from exc
    if not isinstance(result, dict):
        raise RefreshError(f'{label} must be a JSON object')
    return result


@contextmanager
def _cycle_lock(root):
    path = root / 'state' / '.cycle.lock'
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise RefreshError('cycle lock already exists; inspect its PID before taking action') from exc
    try:
        os.write(descriptor, str(os.getpid()).encode('ascii'))
        yield
    finally:
        os.close(descriptor)
        path.unlink(missing_ok=True)


def _parse_csv(value, clock):
    try:
        frame = pd.read_csv(io.BytesIO(value), dtype={'symbol': 'string'})
    except (ValueError, pd.errors.ParserError, UnicodeError) as exc:
        raise RefreshError('market CSV could not be parsed') from exc
    if list(frame.columns) != COLUMNS:
        raise RefreshError('market CSV columns differ from the frozen daily-bar schema')
    return _validate_bars(frame, now=clock)


def _validate_frame(value, clock, label):
    if not isinstance(value, pd.DataFrame) or set(value.columns) != set(COLUMNS):
        raise RefreshError(f'{label} does not contain the full raw OHLCV and adjusted-close schema')
    try:
        return _validate_bars(value, now=clock)
    except (ValueError, TypeError) as exc:
        raise RefreshError(f'{label} failed daily-bar validation: {exc}') from exc


def _keys(frame):
    return pd.MultiIndex.from_arrays([frame['timestamp'], frame['symbol'].to_numpy(dtype=object)], names=['timestamp', 'symbol'])


def _parity(old, fetched):
    old_index, fetched_index = _keys(old), _keys(fetched)
    if not old_index.equals(fetched_index):
        missing = old_index.difference(fetched_index)
        extra = fetched_index.difference(old_index)
        raise RefreshError(f'historical keys changed: missing={len(missing)}, added={len(extra)}; no data promoted')
    if not np.array_equal(old['volume'].to_numpy(), fetched['volume'].to_numpy()):
        count = int((old['volume'].to_numpy() != fetched['volume'].to_numpy()).sum())
        raise RefreshError(f'historical volume changed on {count} rows; no data promoted')
    max_difference = {}
    for field in PRICE_FIELDS:
        before = old[field].to_numpy(dtype=float)
        after = fetched[field].to_numpy(dtype=float)
        differences = np.abs(before-after)
        max_difference[field] = float(differences.max(initial=0.0))
        if not np.all(np.isclose(before, after, rtol=0, atol=PRICE_ABSOLUTE_TOLERANCE)):
            count = int((differences > PRICE_ABSOLUTE_TOLERANCE).sum())
            raise RefreshError(f'historical {field} changed on {count} rows; no data promoted')
    return {'status': 'passed', 'historical_keys_exact': True, 'volume_exact': True,
            'price_absolute_tolerance': PRICE_ABSOLUTE_TOLERANCE, 'price_relative_tolerance': 0,
            'maximum_price_absolute_differences': max_difference, 'rows_compared': len(old)}


def _guard_inputs(root, baseline, config, fingerprint):
    for relative, original in baseline.items():
        if (root / relative).read_bytes() != original:
            raise RefreshError(f'{relative} changed during verification; no data promoted')
    if experiment_hash(root, config) != fingerprint:
        raise RefreshError('frozen strategy/data code changed during verification; no data promoted')


def _write_new(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as handle:
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())


def _stage(parent, value, prefix):
    path = None
    try:
        with tempfile.NamedTemporaryFile(dir=parent, prefix=prefix, suffix='.tmp', delete=False) as handle:
            path = Path(handle.name)
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        return path
    except BaseException:
        # Close the handle before unlinking; Windows refuses open-file deletion.
        if path is not None:
            path.unlink(missing_ok=True)
        raise


def _promote(root, combined_bytes, manifest, baseline, config, fingerprint, token):
    market = root / 'data' / 'market'
    backup = market / 'recovery' / token
    snapshot = Path(manifest['snapshot'])
    stages = []
    try:
        bars_stage = _stage(market, combined_bytes, '.append-bars-')
        stages.append(bars_stage)
        manifest_stage = _stage(market, _json_bytes(manifest), '.append-manifest-')
        stages.append(manifest_stage)
        # Validate precisely the CSV that the frozen paper CLI will read.
        staged = load_bars(bars_stage)
        if len(staged) != manifest['rows']:
            raise RefreshError('staged market CSV row count changed')
        _guard_inputs(root, baseline, config, fingerprint)
        _write_new(backup / 'bars.csv', baseline['data/market/bars.csv'])
        _write_new(backup / 'manifest.json', baseline['data/market/manifest.json'])
        _write_new(backup / 'compatibility.json', _json_bytes(manifest['ingestion_compatibility']))
        _write_new(snapshot, combined_bytes)
        _guard_inputs(root, baseline, config, fingerprint)
        replaced_bars = False
        try:
            os.replace(bars_stage, market / 'bars.csv')
            replaced_bars = True
            os.replace(manifest_stage, market / 'manifest.json')
        except BaseException as exc:
            if replaced_bars:
                try:
                    rollback_bars = _stage(market, baseline['data/market/bars.csv'], '.rollback-bars-')
                    stages.append(rollback_bars)
                    rollback_manifest = _stage(market, baseline['data/market/manifest.json'], '.rollback-manifest-')
                    stages.append(rollback_manifest)
                    os.replace(rollback_bars, market / 'bars.csv')
                    os.replace(rollback_manifest, market / 'manifest.json')
                except BaseException as rollback_exc:
                    raise RefreshError(f'promotion and rollback failed; restore the preserved recovery pair at {backup}') from rollback_exc
            raise RefreshError(f'promotion failed; original input pair restored or unchanged; recovery pair at {backup}') from exc
        return backup
    finally:
        for path in stages:
            path.unlink(missing_ok=True)


def refresh_market(root=ROOT, *, apply=False, now=None, fetcher=None):
    """Check by default; --apply only writes market inputs, never paper state."""
    root = Path(root).resolve()
    clock = pd.Timestamp(now) if now is not None else pd.Timestamp.now(tz='Asia/Shanghai')
    if clock.tzinfo is None:
        raise RefreshError('refresh clock must include a timezone')
    clock = clock.tz_convert('Asia/Shanghai')
    if clock < clock.normalize() + pd.Timedelta(hours=15, minutes=10):
        raise RefreshError('daily refresh deferred until 15:10 Asia/Shanghai')
    fetcher = fetch_tencent if fetcher is None else fetcher
    with _cycle_lock(root):
        baseline = {name: (root / name).read_bytes() for name in
                    ('config.json', 'state/paper.json', 'data/market/bars.csv', 'data/market/manifest.json')}
        config = validate_config(_read_json_bytes(baseline['config.json'], 'configuration'))
        paper = _read_json_bytes(baseline['state/paper.json'], 'paper state')
        manifest = _read_json_bytes(baseline['data/market/manifest.json'], 'market manifest')
        if config.get('data_provider') != 'tencent_public' or manifest.get('provider') != 'tencent_public':
            raise RefreshError('same-source append requires Tencent in both configuration and old manifest')
        old_sha = _hash(baseline['data/market/bars.csv'])
        if old_sha != manifest.get('sha256'):
            raise RefreshError('old market CSV does not match its manifest SHA256')
        fingerprint = experiment_hash(root, config)
        if paper.get('fingerprint') != fingerprint:
            raise RefreshError('paper frozen fingerprint differs from configuration or code; append refused')
        old = _parse_csv(baseline['data/market/bars.csv'], clock)
        if set(old.symbol.unique()) != set(config['symbols']):
            raise RefreshError('old market symbol pool differs from configuration')
        cutoff = old.timestamp.max()
        if pd.Timestamp(manifest.get('latest_session')) != cutoff or manifest.get('rows') != len(old):
            raise RefreshError('old manifest date or row count differs from old market CSV')
        if old.groupby('symbol').timestamp.max().nunique() != 1:
            raise RefreshError('old symbol latest completed dates differ')
        if pd.Timestamp(paper.get('last_session')) != cutoff:
            raise RefreshError('paper last_session differs from old market latest_session; review before append')
        cutoff_day = cutoff.tz_convert('Asia/Shanghai').strftime('%Y-%m-%d')
        requests_record = []
        def fetch(begin, end, label):
            started = datetime.now(timezone.utc).isoformat()
            frame = _validate_frame(fetcher(config['symbols'], begin, end), clock, label)
            requests_record.append({'start': begin, 'end': end, 'purpose': label,
                                    'started_at': started, 'finished_at': datetime.now(timezone.utc).isoformat(),
                                    'rows': len(frame), 'loader': 'frozen_fetch_tencent'})
            return frame
        fetched_old = fetch(config['history_start'], cutoff_day, 'historical_parity')
        parity = _parity(old, fetched_old)
        today = clock.strftime('%Y-%m-%d')
        tail_response = fetch(cutoff_day, today, 'short_tail_with_cutoff_overlap')
        expected_overlap = old.loc[old.timestamp == cutoff].reset_index(drop=True)
        actual_overlap = tail_response.loc[tail_response.timestamp <= cutoff].reset_index(drop=True)
        try:
            overlap_parity = _parity(expected_overlap, actual_overlap)
        except RefreshError as exc:
            raise RefreshError(f'short-tail cutoff overlap is incompatible: {exc}') from exc
        tail = tail_response.loc[tail_response.timestamp > cutoff].reset_index(drop=True)
        if tail.empty:
            # The source returned the known cutoff for every ETF. An empty or
            # failed interface never reaches this verified no-new-data result.
            _guard_inputs(root, baseline, config, fingerprint)
            return {'status': 'no_new_date', 'applied': False, 'ingestion_adapter': ADAPTER,
                    'old_data_sha256': old_sha, 'latest_session': str(cutoff), 'history_parity': parity,
                    'tail_overlap_parity': overlap_parity, 'source_verified': True,
                    'new_rows': 0, 'new_sessions': 0, 'requests': requests_record}
        if set(tail.symbol.unique()) != set(config['symbols']):
            raise RefreshError('new tail is missing a configured symbol')
        date_sets = [set(group.timestamp) for _, group in tail.groupby('symbol')]
        if any(dates != date_sets[0] for dates in date_sets[1:]):
            raise RefreshError('new completed dates differ across symbols; no intersection is taken')
        combined = _validate_frame(pd.concat([old, tail], ignore_index=True), clock, 'combined market input')
        # Retain original CSV bytes, including historical float serialization.
        original = baseline['data/market/bars.csv']
        separator = b'' if original.endswith((b'\n', b'\r')) else b'\n'
        combined_bytes = original + separator + tail[COLUMNS].to_csv(index=False, header=False).encode('utf-8')
        roundtrip = _parse_csv(combined_bytes, clock)
        _parity(old, roundtrip.loc[roundtrip.timestamp <= cutoff].reset_index(drop=True))
        _parity(tail, roundtrip.loc[roundtrip.timestamp > cutoff].reset_index(drop=True))
        if len(roundtrip) != len(combined):
            raise RefreshError('combined CSV serialization changed the row count')
        _guard_inputs(root, baseline, config, fingerprint)
        new_sha = _hash(combined_bytes)
        stamp = datetime.now(timezone.utc)
        token = stamp.strftime('%Y%m%dT%H%M%S%fZ') + '-' + uuid.uuid4().hex[:8]
        snapshot = root / 'data' / 'market' / 'snapshots' / (token + '.csv')
        compatibility = {'status': 'passed', 'adapter': ADAPTER, 'adapter_source_sha256': _hash(Path(__file__).read_bytes()),
                         'frozen_fingerprint': fingerprint, 'old_data_sha256': old_sha,
                         'old_manifest_sha256': _hash(baseline['data/market/manifest.json']),
                         'paper_before_sha256': _hash(baseline['state/paper.json']),
                         'old_latest_session': str(cutoff), 'old_history_bytes_preserved': True,
                         'history_parity': parity, 'tail_overlap_parity': overlap_parity, 'source_verified': True,
                         'requests': requests_record,
                         'new_rows': len(tail), 'new_sessions': len(date_sets[0]),
                         'no_paper_or_pending_mutation': True,
                         'limitation': 'Same-source input compatibility, not corporate-action verification or broker fills.'}
        updated = {'provider': 'tencent_public', 'fetched_at': stamp.isoformat(),
                   'latest_session': str(combined.timestamp.max()), 'rows': len(combined),
                   'volume_unit': 'shares', 'execution_prices': 'unadjusted',
                   'signal_prices': 'current_snapshot_qfq', 'snapshot': str(snapshot), 'sha256': new_sha,
                   'ingestion_adapter': ADAPTER, 'ingestion_compatibility': compatibility}
        summary = {'status': 'ready', 'applied': False, 'ingestion_adapter': ADAPTER,
                   'old_data_sha256': old_sha, 'new_data_sha256': new_sha,
                   'old_latest_session': str(cutoff), 'latest_session': updated['latest_session'],
                   'old_rows': len(old), 'new_rows': len(tail), 'new_sessions': len(date_sets[0]),
                   'history_parity': parity, 'tail_overlap_parity': overlap_parity,
                   'source_verified': True, 'paper_unchanged': True}
        if apply:
            backup = _promote(root, combined_bytes, updated, baseline, config, fingerprint, token)
            summary.update(status='applied', applied=True, snapshot=str(snapshot), recovery=str(backup))
        return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='promote verified market inputs; never advances paper')
    args = parser.parse_args(argv)
    try:
        result = refresh_market(ROOT, apply=args.apply)
    except requests.RequestException as exc:
        result = {'status': 'error', 'applied': False, 'error': f'network fetch failed ({type(exc).__name__})'}
    except (OSError, ValueError, TypeError, KeyError) as exc:
        result = {'status': 'error', 'applied': False, 'error': str(exc)}
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 1 if result['status'] == 'error' else 0


if __name__ == '__main__':
    raise SystemExit(main())