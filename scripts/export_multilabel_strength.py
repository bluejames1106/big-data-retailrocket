"""Export validated score-fusion serving bundle without overwriting prior runs."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import shutil

from pipeline.config import atomic_json


ROOT = Path(__file__).resolve().parents[1]
OLD = ROOT / 'outputs/runs/remaining-20261022-v1'
NEW = ROOT / 'outputs/runs/strength-20261022-v1'


def run():
    validation = json.loads((ROOT / 'outputs/evidence/multilabel_strength_validation.json').read_text(encoding='utf-8'))
    heldout = json.loads((ROOT / 'outputs/evidence/multilabel_strength_heldout.json').read_text(encoding='utf-8'))
    assert validation['selected'] == heldout['selected_configuration']['name'] == 'strength_0.25_0.5'
    assert validation['selected_improves_all_three_on_validation']
    for metric in ('precision_at_k', 'recall_at_k', 'ndcg_at_k'):
        assert heldout['candidate'][metric] > heldout['baseline'][metric]
    if NEW.exists():
        raise FileExistsError(NEW)
    serving = NEW / 'serving'
    serving.mkdir(parents=True)
    for name in ('session_neighbors.sqlite', 'seen_items.sqlite'):
        shutil.copy2(OLD / 'serving' / name, serving / name)
    original = json.loads((OLD / 'serving/model_bundle.json').read_text(encoding='utf-8'))
    version = NEW.name + ':session-serving-v1'
    now = datetime.now(timezone.utc).isoformat()
    bundle = {**original, 'run_id': NEW.name, 'model_version': version, 'trained_at_utc': now}
    previous = original['session_model']
    bundle['session_model'] = {**previous,
        'model_version': version + ':session',
        'metric_model_version': NEW.name + ':remaining-session-evaluation-v1',
        'reference_run_id': OLD.name,
        'configuration': {**previous['configuration'], 'name': 'strength_0.25_0.5',
                          'fusion': 'strength', 'strength_power': .5, 'transition_weight': .25},
        'metrics': {'protocol_kind': 'remaining_session', 'protocol': heldout['protocol'], 'k': 10,
                    'selected': heldout['candidate'], 'current_item_item': heldout['baseline'],
                    'popularity': previous['metrics']['popularity'],
                    'selected_configuration': heldout['selected_configuration'],
                    'selected_model_promoted': True, 'served_policy': 'strength_0.25_0.5',
                    'label_profile': heldout['labels'],
                    'next_item': {'served': heldout['next_item']['candidate'],
                                  'previous': heldout['next_item']['baseline'],
                                  'protocol': 'one next-new-item per eligible session'},
                    'paired_visitor_cluster_bootstrap_95': heldout['paired_visitor_cluster_bootstrap_95'],
                    'test_reuse_disclosure': heldout['test_reuse_disclosure']}}
    atomic_json(serving / 'model_bundle.json', bundle)
    metrics = NEW / 'metrics'
    metrics.mkdir()
    shutil.copy2(OLD / 'metrics/kpis.json', metrics / 'kpis.json')
    atomic_json(metrics / 'run_report.json', {'run_id': NEW.name, 'source_run_id': OLD.name,
                'source_data_run_id': 'full-20261022-1031', 'status': 'completed_not_activated',
                'completed_at_utc': now,
                'scope': 'Normalized edge-strength session fusion; unchanged static bundle',
                'test_reuse_disclosure': heldout['test_reuse_disclosure']})
    atomic_json(metrics / 'session_validation_selection.json', validation)
    atomic_json(metrics / 'session_heldout_comparison.json', heldout)
    print(json.dumps({'run_id': NEW.name, 'model_version': version,
                      'served_policy': 'strength_0.25_0.5', 'metrics': heldout['candidate']}, indent=2), flush=True)


if __name__ == '__main__':
    run()
