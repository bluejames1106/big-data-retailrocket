"""One historical held-out evaluation of the multi-label session protocol."""
from __future__ import annotations

import json
from pathlib import Path

from pipeline.session_model import context_affinities
from pipeline.session_multilabel import remaining_session_labels
from pipeline.session_transition import fit_transitions, fused_rank
from pipeline.session_upgrade import CURRENT, EventData, SparseGraph, canonical_hash, clustered_bootstrap, summarize
from scripts.experiment_session_coverage import load_clean_csv


ROOT = Path(__file__).resolve().parents[1]
METRICS = ROOT / 'outputs/runs/session-20261022-1327/metrics'
OUT = ROOT / 'outputs/evidence/session_multilabel_heldout.json'


def run():
    validation = json.loads((ROOT / 'outputs/evidence/session_multilabel_validation.json').read_text())
    weight = validation['selected_transition_weight']
    assert weight == .2 and validation['selected_improves_all_three_on_validation']
    _, arrays = load_clean_csv()
    data = EventData(arrays)
    split = json.loads((METRICS / 'temporal_split.json').read_text())
    cutoff = split['boundaries'][-1]
    end = int(data.a['timestamp'].max())
    rows, population = data.session_rows(cutoff, end)
    recorded = json.loads((METRICS / 'session_test_comparison.json').read_text())
    source_hash = canonical_hash(rows)
    assert source_hash == recorded['cohort_sha256']
    labels = remaining_session_labels(data, rows, end)
    pop = data.popularity(cutoff)
    seeds = {seed for row in rows for seed, _ in context_affinities(row['context'], CURRENT, row['as_of'])}
    graph = SparseGraph(data, cutoff, 'binary_user', binary_similarity='conditional', shrinkage=3)
    co = graph.neighbors(seeds)
    trans, profile = fit_transitions(data, cutoff)
    trans = {seed: trans.get(seed, []) for seed in seeds}
    for row in rows:
        for name, trial_weight in (('current_item_item', .25), ('selected', weight)):
            result = fused_rank(co, trans, row['context'], pop, row['seen'], trial_weight, 10, CURRENT, row['as_of'])
            row[name] = [r['itemid'] for r in result['items']]
        row['popularity'] = [item for item in pop if item not in set(row['seen'])][:10]
    baseline = summarize(rows, 'current_item_item')
    candidate = summarize(rows, 'selected')
    popularity = summarize(rows, 'popularity')
    report = {'protocol': 'all distinct new items from query time through end of same session, capped at evaluation period end',
              'scope': 'Previously viewed final time window; new protocol and fusion weight frozen before this check.',
              'source_cohort_sha256': source_hash, 'population': population, 'labels': labels,
              'transition_weight': weight, 'transition_profile': profile,
              'baseline': baseline, 'candidate': candidate, 'popularity': popularity,
              'paired_visitor_cluster_bootstrap_95': clustered_bootstrap(rows),
              'test_reuse_disclosure': 'Historical test was already viewed; future-period evidence is still needed.'}
    OUT.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2), flush=True)


if __name__ == '__main__':
    run()
