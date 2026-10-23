"""Try small recent-popularity fusion weights on multi-label validation."""
from __future__ import annotations

import json
from pathlib import Path

from pipeline.session_model import context_affinities
from pipeline.session_multilabel import remaining_session_labels
from pipeline.session_transition import fit_transitions, popularity_blend_rank
from pipeline.session_upgrade import CURRENT, EventData, SparseGraph, canonical_hash, summarize
from scripts.experiment_session_coverage import load_clean_csv


ROOT = Path(__file__).resolve().parents[1]
METRICS = ROOT / 'outputs/runs/session-20261022-1327/metrics'
OUT = ROOT / 'outputs/evidence/multilabel_popularity_validation.json'
WEIGHTS = (0, .01, .025, .05, .1, .2)


def run():
    _, arrays = load_clean_csv()
    data = EventData(arrays)
    split = json.loads((METRICS / 'temporal_split.json').read_text())
    previous = json.loads((ROOT / 'outputs/evidence/session_multilabel_validation.json').read_text())
    folds = []
    for index, (cutoff, end) in enumerate(zip(split['boundaries'], split['boundaries'][1:]), 1):
        rows, _ = data.session_rows(cutoff, end)
        assert canonical_hash(rows) == previous['folds'][index-1]['source_cohort_sha256']
        remaining_session_labels(data, rows, end)
        pop = data.popularity(cutoff)
        seeds = {seed for row in rows for seed, _ in context_affinities(row['context'], CURRENT, row['as_of'])}
        graph = SparseGraph(data, cutoff, 'binary_user', binary_similarity='conditional', shrinkage=3)
        co = graph.neighbors(seeds)
        trans, _ = fit_transitions(data, cutoff)
        trans = {seed: trans.get(seed, []) for seed in seeds}
        trials = []
        for weight in WEIGHTS:
            predictions = []
            for row in rows:
                result = popularity_blend_rank(co, trans, row['context'], pop, row['seen'],
                                               weight, 10, CURRENT, row['as_of'])
                predictions.append({**row, 'trial': [r['itemid'] for r in result['items']]})
            metrics = summarize(predictions, 'trial')
            if weight == 0:
                baseline = next(r['metrics'] for r in previous['folds'][index-1]['trials']
                                if r['transition_weight'] == .25)
                assert metrics['total_hits'] == baseline['total_hits']
                assert abs(metrics['ndcg_at_k'] - baseline['ndcg_at_k']) < 1e-10
            trials.append({'popularity_weight': weight, 'metrics': metrics})
            print(f'FOLD {index} POP {weight}: hits={metrics["total_hits"]} '
                  f'P={metrics["precision_at_k"]:.5f} R={metrics["recall_at_k"]:.5f} '
                  f'NDCG={metrics["ndcg_at_k"]:.5f}', flush=True)
        folds.append({'fold': index, 'trials': trials})
    means = {}
    for weight in WEIGHTS:
        values = [next(r['metrics'] for r in fold['trials'] if r['popularity_weight'] == weight) for fold in folds]
        means[str(weight)] = {metric: sum(r[metric] for r in values)/len(values)
                              for metric in ('precision_at_k', 'recall_at_k', 'ndcg_at_k')}
    selected = max(WEIGHTS, key=lambda w: (means[str(w)]['ndcg_at_k'], means[str(w)]['recall_at_k'], -w))
    improves_all = all(means[str(selected)][metric] > means[str(0)][metric] + 1e-12
                       for metric in ('precision_at_k', 'recall_at_k', 'ndcg_at_k'))
    report = {'scope': 'Two frozen chronological multi-label validation folds only.',
              'folds': folds, 'mean_validation_metrics': means,
              'selected_popularity_weight': selected,
              'selected_improves_all_three_on_validation': improves_all}
    OUT.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(f'SELECTED {selected}; ALL THREE IMPROVED {improves_all}; WROTE {OUT}', flush=True)


if __name__ == '__main__':
    run()
