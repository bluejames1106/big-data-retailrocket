"""Select transition fusion weight using only two chronological validation folds."""
from __future__ import annotations

import json
from pathlib import Path

from pipeline.session_model import context_affinities
from pipeline.session_transition import fit_transitions, fused_rank
from pipeline.session_upgrade import CURRENT, EventData, SparseGraph, canonical_hash, summarize
from scripts.experiment_session_coverage import load_clean_csv


ROOT = Path(__file__).resolve().parents[1]
METRICS = ROOT / 'outputs/runs/session-20261022-1327/metrics'
OUT = ROOT / 'outputs/evidence/session_transition_validation.json'
WEIGHTS = (0, .25, .5, .75, 1)


def run():
    count, arrays = load_clean_csv()
    assert count == 2756101 and len(arrays['timestamp']) == 2755641
    data = EventData(arrays)
    split = json.loads((METRICS / 'temporal_split.json').read_text())
    previous = json.loads((ROOT / 'outputs/evidence/session_similarity_experiment.json').read_text())
    folds = []
    for index, (cutoff, end) in enumerate(zip(split['boundaries'], split['boundaries'][1:]), 1):
        rows, population = data.session_rows(cutoff, end)
        digest = canonical_hash(rows)
        assert digest == json.loads((METRICS / f'session_validation_fold_{index}.json').read_text())['cohort_sha256']
        pop = data.popularity(cutoff)
        seeds = {s for r in rows for s, _ in context_affinities(r['context'], CURRENT, r['as_of'])}
        graph = SparseGraph(data, cutoff, 'binary_user', binary_similarity='conditional', shrinkage=3)
        co = graph.neighbors(seeds)
        trans, profile = fit_transitions(data, cutoff)
        trans = {seed: trans.get(seed, []) for seed in seeds}
        trials = []
        for weight in WEIGHTS:
            predictions = []
            for row in rows:
                result = fused_rank(co, trans, row['context'], pop, row['seen'], weight, 10, CURRENT, row['as_of'])
                predictions.append({**row, 'trial': [r['itemid'] for r in result['items']]})
            metrics = summarize(predictions, 'trial')
            if weight == 0:
                prior = next(r['metrics'] for r in previous['folds'][index-1]['trials']
                             if r['similarity'] == 'conditional' and r['shrinkage'] == 3)
                assert metrics['total_hits'] == prior['total_hits']
                assert abs(metrics['ndcg_at_k'] - prior['ndcg_at_k']) < 1e-10
            trials.append({'transition_weight': weight, 'metrics': metrics})
            print(f'FOLD {index} WEIGHT {weight}: hits={metrics["total_hits"]} '
                  f'NDCG={metrics["ndcg_at_k"]:.5f}', flush=True)
        folds.append({'fold': index, 'cohort_sha256': digest, 'population': population,
                      'transition_profile': profile, 'trials': trials})
    means = {}
    for weight in WEIGHTS:
        selected = [next(t['metrics'] for t in fold['trials'] if t['transition_weight'] == weight) for fold in folds]
        means[str(weight)] = {key: sum(t[key] for t in selected)/len(selected)
                              for key in ('precision_at_k', 'recall_at_k', 'ndcg_at_k')}
    chosen = max(WEIGHTS, key=lambda w: (means[str(w)]['ndcg_at_k'], means[str(w)]['recall_at_k'], -w))
    report = {'scope': 'Two chronological validation folds only; test unread.', 'folds': folds,
              'mean_validation_metrics': means, 'selected_transition_weight': chosen}
    OUT.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(f'SELECTED {chosen}; WROTE {OUT}', flush=True)


if __name__ == '__main__':
    run()
