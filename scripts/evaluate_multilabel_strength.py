"""One final historical check of validation-selected normalized-strength fusion."""
from __future__ import annotations

import json
from pathlib import Path

from pipeline.session_model import context_affinities
from pipeline.session_multilabel import remaining_session_labels
from pipeline.session_transition import fit_transitions, fused_rank, strength_fused_rank
from pipeline.session_upgrade import CURRENT, EventData, SparseGraph, canonical_hash, clustered_bootstrap, summarize
from scripts.experiment_session_coverage import load_clean_csv


ROOT = Path(__file__).resolve().parents[1]
METRICS = ROOT / 'outputs/runs/session-20261022-1327/metrics'
OUT = ROOT / 'outputs/evidence/multilabel_strength_heldout.json'


def run():
    validation = json.loads((ROOT / 'outputs/evidence/multilabel_strength_validation.json').read_text())
    assert validation['selected'] == 'strength_0.25_0.5'
    assert validation['selected_improves_all_three_on_validation']
    _, arrays = load_clean_csv()
    data = EventData(arrays)
    split = json.loads((METRICS / 'temporal_split.json').read_text())
    cutoff, end = split['boundaries'][-1], int(data.a['timestamp'].max())
    rows, population = data.session_rows(cutoff, end)
    source_hash = canonical_hash(rows)
    assert source_hash == json.loads((METRICS / 'session_test_comparison.json').read_text())['cohort_sha256']
    pop = data.popularity(cutoff)
    seeds = {seed for row in rows for seed, _ in context_affinities(row['context'], CURRENT, row['as_of'])}
    graph = SparseGraph(data, cutoff, 'binary_user', binary_similarity='conditional', shrinkage=3)
    co = graph.neighbors(seeds)
    trans, profile = fit_transitions(data, cutoff)
    trans = {seed: trans.get(seed, []) for seed in seeds}
    for row in rows:
        base = fused_rank(co, trans, row['context'], pop, row['seen'], .25, 10, CURRENT, row['as_of'])
        candidate = strength_fused_rank(co, trans, row['context'], pop, row['seen'], .25, .5,
                                        10, CURRENT, row['as_of'])
        row['current_item_item'] = [r['itemid'] for r in base['items']]
        row['selected'] = [r['itemid'] for r in candidate['items']]
    next_item = {'baseline': summarize(rows, 'current_item_item'), 'candidate': summarize(rows, 'selected')}
    assert next_item['baseline']['total_hits'] == 1502
    labels = remaining_session_labels(data, rows, end)
    base = summarize(rows, 'current_item_item')
    candidate = summarize(rows, 'selected')
    previous = json.loads((ROOT / 'outputs/evidence/session_multilabel_heldout.json').read_text())
    assert base['total_hits'] == previous['baseline']['total_hits']
    assert abs(base['ndcg_at_k'] - previous['baseline']['ndcg_at_k']) < 1e-10
    report = {'protocol': 'all distinct new items from query time through remaining session',
              'scope': 'Previously viewed final time window; strength fusion frozen on two validation folds.',
              'source_cohort_sha256': source_hash, 'population': population, 'labels': labels,
              'selected_configuration': {'name': 'strength_0.25_0.5', 'transition_weight': .25, 'power': .5},
              'transition_profile': profile, 'baseline': base, 'candidate': candidate,
              'next_item': next_item,
              'paired_visitor_cluster_bootstrap_95': clustered_bootstrap(rows),
              'test_reuse_disclosure': 'Historical test was already viewed; future-period evidence is still needed.'}
    OUT.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2), flush=True)


if __name__ == '__main__':
    run()
