"""Export a separate full-history demo snapshot with transition fusion."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sqlite3

from pipeline.config import atomic_json
from pipeline.session_transition import fit_transitions
from pipeline.session_upgrade import EventData
from scripts.experiment_session_coverage import load_clean_csv


ROOT = Path(__file__).resolve().parents[1]
OLD = ROOT / 'outputs/runs/conditional-20261022-v1'
NEW = ROOT / 'outputs/runs/transition-20261022-v1'


def run():
    validation = json.loads((ROOT / 'outputs/evidence/session_transition_validation.json').read_text())
    heldout = json.loads((ROOT / 'outputs/evidence/session_transition_heldout.json').read_text())
    assert validation['selected_transition_weight'] == heldout['transition_weight'] == .25
    for fold in validation['folds']:
        scores = {r['transition_weight']: r['metrics'] for r in fold['trials']}
        for metric in ('precision_at_k', 'recall_at_k', 'ndcg_at_k'):
            assert scores[.25][metric] > scores[0][metric]
    for metric in ('precision_at_k', 'recall_at_k', 'ndcg_at_k'):
        assert heldout['candidate'][metric] > heldout['baseline'][metric]
    if NEW.exists():
        raise FileExistsError(NEW)
    serving = NEW / 'serving'
    serving.mkdir(parents=True)
    index = serving / 'session_neighbors.sqlite'
    shutil.copy2(OLD / 'serving/session_neighbors.sqlite', index)
    shutil.copy2(OLD / 'serving/seen_items.sqlite', serving / 'seen_items.sqlite')
    _, arrays = load_clean_csv()
    data = EventData(arrays)
    transitions, profile = fit_transitions(data, int(data.a['timestamp'].max()))
    with closing(sqlite3.connect(index)) as db:
        db.execute('CREATE TABLE transitions(seed INTEGER,candidate INTEGER,score REAL NOT NULL,PRIMARY KEY(seed,candidate)) WITHOUT ROWID')
        for seed, edges in transitions.items():
            db.executemany('INSERT INTO transitions VALUES (?,?,?)',
                           ((seed, item, float(score)) for item, score in edges))
        db.commit()
        integrity = db.execute('PRAGMA quick_check').fetchone()[0]
        count = db.execute('SELECT COUNT(*) FROM transitions').fetchone()[0]
    assert integrity == 'ok' and count > 0
    original = json.loads((OLD / 'serving/model_bundle.json').read_text(encoding='utf-8'))
    version = NEW.name + ':session-serving-v1'
    now = datetime.now(timezone.utc).isoformat()
    bundle = {**original, 'run_id': NEW.name, 'model_version': version, 'trained_at_utc': now}
    bundle['session_model'] = {
        'model_version': version + ':session', 'metric_model_version': NEW.name + ':session-evaluation-v1',
        'index_file': 'session_neighbors.sqlite', 'reference_run_id': OLD.name,
        'configuration': {**original['session_model']['configuration'], 'name': 'conditional_transition_25',
                          'transition_weight': .25},
        'metrics': {'protocol': 'session next-new-item conditional query; one target per eligible session',
                    'k': 10, 'current_item_item': heldout['baseline'], 'selected': heldout['candidate'],
                    'popularity': original['session_model']['metrics']['popularity'],
                    'selected_configuration': {'name': 'conditional_transition_25', 'transition_weight': .25},
                    'served_policy': 'conditional_transition_25', 'selected_model_promoted': True,
                    'test_reuse_disclosure': heldout['test_reuse_disclosure'],
                    'paired_visitor_cluster_bootstrap_95': heldout['paired_visitor_cluster_bootstrap_95']},
        'index_profile': {**original['session_model']['index_profile'], 'transition': {**profile, 'stored_edges': count,
                                                                                       'sqlite_integrity': integrity}},
    }
    atomic_json(serving / 'model_bundle.json', bundle)
    metrics = NEW / 'metrics'
    metrics.mkdir()
    shutil.copy2(OLD / 'metrics/kpis.json', metrics / 'kpis.json')
    atomic_json(metrics / 'run_report.json', {'run_id': NEW.name, 'source_run_id': OLD.name,
                'source_data_run_id': 'full-20261022-1031', 'status': 'completed_not_activated',
                'completed_at_utc': now, 'scope': 'Full-history transition graph; unchanged static bundle',
                'test_reuse_disclosure': heldout['test_reuse_disclosure']})
    atomic_json(metrics / 'session_validation_selection.json', validation)
    atomic_json(metrics / 'session_heldout_comparison.json', heldout)
    print(json.dumps({'run_id': NEW.name, 'model_version': version,
                      'transition_edges': count, 'transition_profile': profile,
                      'sqlite_integrity': integrity}, indent=2), flush=True)


if __name__ == '__main__':
    run()
