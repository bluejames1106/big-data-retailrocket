"""Read-only HTTP and artifact checks for a newly extracted demo ZIP."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import statistics
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root', type=Path, required=True)
    parser.add_argument('--base-url', default='http://127.0.0.1:8002')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = args.project_root.resolve()
    pointer = json.loads((root / 'outputs/latest_run.json').read_text(encoding='utf-8-sig'))
    run = root / 'outputs/runs' / pointer['run_id']
    protected = [root / 'data/raw/events.csv', root / 'outputs/latest_run.json',
                 *[run / 'serving' / name for name in
                   ('model_bundle.json', 'seen_items.sqlite', 'session_neighbors.sqlite')],
                 *[run / 'metrics' / name for name in
                   ('session_heldout_comparison.json', 'session_validation_selection.json')]]
    def hashes():
        result = {}
        for path in protected:
            with path.open('rb') as stream:
                result[path.relative_to(root).as_posix()] = hashlib.file_digest(stream, 'sha256').hexdigest()
        return result
    before = hashes()
    def request(path, payload=None):
        body = None if payload is None else json.dumps(payload).encode()
        with urlopen(Request(args.base_url.rstrip('/') + path, data=body,
                             headers={'Content-Type': 'application/json'}), timeout=30) as response:
            return json.load(response)
    ready = request('/health/ready')
    assert ready['status'] == 'ready' and not ready['model']['stale']
    assert all(value['status'] == 'ok' for value in ready['dependencies'].values())
    metrics = request('/api/metrics')
    assert metrics['run_id'] == pointer['run_id'] and metrics['model_version'] == pointer['model_version']
    assert metrics['session_model']['available'] and metrics['kpis'] is not None
    assert metrics['session_model']['metric_model_version'] != metrics['session_model']['model_version']
    static = {}
    for path in ('/', '/docs', '/openapi.json', '/static/app.js', '/static/storefront.css',
                 '/static/product-catalog.js', '/static/images/catalog/laptop.svg'):
        with urlopen(args.base_url.rstrip('/') + path, timeout=30) as response:
            static[path] = {'status': response.status, 'bytes': len(response.read())}
        assert static[path]['status'] == 200 and static[path]['bytes'] > 0
    def session(events, visitorid=None):
        payload = {'events': events, 'k': 10}
        if visitorid is not None:
            payload['visitorid'] = visitorid
        reply = request('/api/session/recommendations', payload)
        ids = [value['itemid'] for value in reply['items']]
        assert len(ids) == len(set(ids)) == 10
        assert not set(ids) & {value['itemid'] for value in events}
        assert reply['run_id'] == pointer['run_id'] and reply['persistence'] == 'client_context_only'
        return reply
    first = session([])
    view = session([{'itemid': 129960, 'event': 'view', 'timestamp': 1441329333729}])
    purchase = session([{'itemid': 129960, 'event': 'view'}, {'itemid': 320130, 'event': 'transaction'}])
    assert view['source'] == 'session_strength_fusion'
    assert first['items'] != view['items'] and purchase['items'] != view['items']
    assert session([])['items'] == first['items']
    known = session([{'itemid': 129960, 'event': 'view'}], 1000484)
    with closing(sqlite3.connect((run / 'serving/seen_items.sqlite').resolve().as_uri() + '?mode=ro', uri=True)) as db:
        seen = {value[0] for value in db.execute('SELECT itemid FROM seen_items WHERE visitorid=?', (1000484,))}
    assert seen and not seen & {value['itemid'] for value in known['items']}
    try:
        session([{'itemid': 1, 'event': 'view', 'timestamp': int(time.time() * 1000) + 60000}])
    except HTTPError as error:
        assert error.code == 422
    else:
        raise AssertionError('Future timestamp accepted')
    def measured(index):
        start = time.perf_counter()
        session([{'itemid': 129960 if index % 2 else 17114, 'event': 'view'}])
        return (time.perf_counter() - start) * 1000
    for index in range(3):
        measured(index)
    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=4) as pool:
        latency = sorted(pool.map(measured, range(100)))
    elapsed = time.perf_counter() - start
    after_metrics = request('/api/metrics')
    assert after_metrics == metrics, 'Clicks unexpectedly changed historical model metrics/KPIs.'
    assert hashes() == before, 'A protected source/model/evaluation artifact changed.'
    result = {'status': 'passed', 'checked_at_utc': datetime.now(timezone.utc).isoformat(),
              'run_id': pointer['run_id'], 'base_url': args.base_url,
              'scope': 'Snapshot demo from release ZIP, not full Kafka/Spark/HDFS/Airflow rerun.',
              'health': ready, 'static_resources': static,
              'checks': {'model_version': True, 'session_model_available': True, 'kpis_available': True,
                         'click_changes_ranking': True, 'sessions_isolated': True,
                         'seen_and_duplicate_filter': True, 'known_user_full_seen_filter': True,
                         'future_timestamp_rejected': True, 'historical_metrics_unchanged': True,
                         'seven_protected_artifacts_unchanged': True},
              'artifact_sha256': before,
              'benchmark': {'requests': 100, 'successful': 100, 'concurrency': 4, 'warmups': 3,
                            'elapsed_seconds': elapsed, 'latency_ms': {'mean': statistics.mean(latency),
                                'p50': statistics.median(latency), 'p95': latency[94], 'p99': latency[98]},
                            'scope': 'Windows host -> local Docker HTTP, two fixed seeds; not production SLA.'},
              'examples': {'empty': first, 'view': view, 'view_then_purchase': purchase, 'known_user': known}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: value for key, value in result.items() if key not in ('examples', 'artifact_sha256')}, indent=2))


if __name__ == '__main__':
    main()
