"""One final historical test of validation-selected transition fusion."""
from __future__ import annotations

import json
from pathlib import Path

from pipeline.session_model import context_affinities
from pipeline.session_transition import fit_transitions, fused_rank
from pipeline.session_upgrade import CURRENT, EventData, SparseGraph, canonical_hash, clustered_bootstrap, summarize
from scripts.experiment_session_coverage import load_clean_csv


ROOT = Path(__file__).resolve().parents[1]
METRICS = ROOT / 'outputs/runs/session-20261022-1327/metrics'
OUT = ROOT / 'outputs/evidence/session_transition_heldout.json'


def run():
    validation = json.loads((ROOT / 'outputs/evidence/session_transition_validation.json').read_text())
    weight = validation['selected_transition_weight']
    assert weight == .25
    for fold in validation['folds']:
        scores = {r['transition_weight']: r['metrics'] for r in fold['trials']}
        for metric in ('precision_at_k', 'recall_at_k', 'ndcg_at_k'):
            assert scores[weight][metric] > scores[0][metric]
    _, arrays = load_clean_csv()
    data = EventData(arrays)
    split = json.loads((METRICS / 'temporal_split.json').read_text())
    cutoff = split['boundaries'][-1]
    rows, population = data.session_rows(cutoff, int(data.a['timestamp'].max()))
    recorded = json.loads((METRICS / 'session_test_comparison.json').read_text())
    assert canonical_hash(rows) == recorded['cohort_sha256']
    pop = data.popularity(cutoff)
    seeds = {s for row in rows for s, _ in context_affinities(row['context'], CURRENT, row['as_of'])}
    graph = SparseGraph(data, cutoff, 'binary_user', binary_similarity='conditional', shrinkage=3)
    co = graph.neighbors(seeds)
    trans, profile = fit_transitions(data, cutoff)
    trans = {seed: trans.get(seed, []) for seed in seeds}
    for row in rows:
        base = fused_rank(co, trans, row['context'], pop, row['seen'], 0, 10, CURRENT, row['as_of'])
        candidate = fused_rank(co, trans, row['context'], pop, row['seen'], weight, 10, CURRENT, row['as_of'])
        row['current_item_item'] = [r['itemid'] for r in base['items']]
        row['selected'] = [r['itemid'] for r in candidate['items']]
    baseline = summarize(rows, 'current_item_item')
    conditional = json.loads((ROOT / 'outputs/evidence/session_conditional_heldout.json').read_text())['candidate']
    assert baseline['total_hits'] == conditional['total_hits']
    assert abs(baseline['ndcg_at_k'] - conditional['ndcg_at_k']) < 1e-10
    candidate = summarize(rows, 'selected')
    report = {'scope': 'Previously viewed final time window; fusion weight frozen on validation.',
              'transition_weight': weight, 'transition_profile': profile, 'cohort_sha256': canonical_hash(rows),
              'population': population, 'baseline': baseline, 'candidate': candidate,
              'paired_visitor_cluster_bootstrap_95': clustered_bootstrap(rows),
              'test_reuse_disclosure': 'Historical test was already viewed; future-period evidence is still needed.'}
    OUT.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2), flush=True)


if __name__ == '__main__':
    run()
