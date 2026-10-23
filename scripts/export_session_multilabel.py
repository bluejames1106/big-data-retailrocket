"""Publish multi-label evaluation in a new snapshot, retaining the stronger served model."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import shutil

from pipeline.config import atomic_json


ROOT = Path(__file__).resolve().parents[1]
OLD = ROOT / 'outputs/runs/transition-20261022-v1'
NEW = ROOT / 'outputs/runs/remaining-20261022-v1'


def run():
    validation = json.loads((ROOT / 'outputs/evidence/session_multilabel_validation.json').read_text(encoding='utf-8'))
    heldout = json.loads((ROOT / 'outputs/evidence/session_multilabel_heldout.json').read_text(encoding='utf-8'))
    assert validation['selected_transition_weight'] == heldout['transition_weight'] == .2
    assert validation['selected_improves_all_three_on_validation']
    assert heldout['candidate']['recall_at_k'] < heldout['baseline']['recall_at_k']
    assert heldout['candidate']['ndcg_at_k'] < heldout['baseline']['ndcg_at_k']
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
    bundle['session_model'] = {**previous, 'model_version': version + ':session',
                               'metric_model_version': NEW.name + ':remaining-session-evaluation-v1',
                               'reference_run_id': OLD.name,
                               'metrics': {'protocol_kind': 'remaining_session', 'protocol': heldout['protocol'],
                                           'k': 10, 'selected': heldout['candidate'],
                                           'current_item_item': heldout['baseline'],
                                           'popularity': heldout['popularity'],
                                           'selected_configuration': {'name': 'transition_weight_20_candidate',
                                                                      'transition_weight': .2},
                                           'selected_model_promoted': False,
                                           'served_policy': previous['metrics']['served_policy'],
                                           'label_profile': heldout['labels'],
                                           'next_item': {'served': previous['metrics']['selected'],
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
                'scope': 'Multi-label session evaluation; serving graph remains transition weight .25',
                'test_reuse_disclosure': heldout['test_reuse_disclosure']})
    atomic_json(metrics / 'session_validation_selection.json', validation)
    atomic_json(metrics / 'session_heldout_comparison.json', heldout)
    print(json.dumps({'run_id': NEW.name, 'model_version': version,
                      'served_policy': previous['metrics']['served_policy'],
                      'multi_label_metrics': heldout['baseline'],
                      'next_item_metrics': previous['metrics']['selected']}, indent=2), flush=True)


if __name__ == '__main__':
    run()
