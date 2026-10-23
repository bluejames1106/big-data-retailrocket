"""Try remaining-session associations on frozen multi-label validation folds."""
from __future__ import annotations

import json
from pathlib import Path

from pipeline.session_model import context_affinities
from pipeline.session_multilabel import remaining_session_labels
from pipeline.session_transition import fit_transitions, fit_continuations, continuation_rank
from pipeline.session_upgrade import CURRENT, EventData, SparseGraph, canonical_hash, summarize
from scripts.experiment_session_coverage import load_clean_csv


ROOT = Path(__file__).resolve().parents[1]
METRICS = ROOT / 'outputs/runs/session-20261022-1327/metrics'
OUT = ROOT / 'outputs/evidence/session_continuation_validation.json'
WEIGHTS = (0, .1, .2, .3, .4, .5, .75, 1)


def run():
    _, arrays = load_clean_csv()
    assert len(arrays['timestamp']) == 2755641
    data = EventData(arrays)
    split = json.loads((METRICS / 'temporal_split.json').read_text())
    previous = json.loads((ROOT / 'outputs/evidence/session_multilabel_validation.json').read_text())
    folds = []
    for index, (cutoff, end) in enumerate(zip(split['boundaries'], split['boundaries'][1:]), 1):
        rows, _ = data.session_rows(cutoff, end)
        digest = canonical_hash(rows)
        assert digest == previous['folds'][index-1]['source_cohort_sha256']
        labels = remaining_session_labels(data, rows, end)
        pop = data.popularity(cutoff)
        seeds = {seed for row in rows for seed, _ in context_affinities(row['context'], CURRENT, row['as_of'])}
        graph = SparseGraph(data, cutoff, 'binary_user', binary_similarity='conditional', shrinkage=3)
        co = graph.neighbors(seeds)
        trans, _ = fit_transitions(data, cutoff)
        cont, profile = fit_continuations(data, cutoff)
        trans = {seed: trans.get(seed, []) for seed in seeds}
        cont = {seed: cont.get(seed, []) for seed in seeds}
        trials = []
        for weight in WEIGHTS:
            predictions = []
            for row in rows:
                result = continuation_rank(co, trans, cont, row['context'], pop, row['seen'],
                                           weight, 10, CURRENT, row['as_of'])
                predictions.append({**row, 'trial': [r['itemid'] for r in result['items']]})
            metrics = summarize(predictions, 'trial')
            if weight == 0:
                baseline = next(r['metrics'] for r in previous['folds'][index-1]['trials']
                                if r['transition_weight'] == .25)
                assert metrics['total_hits'] == baseline['total_hits']
                assert abs(metrics['ndcg_at_k'] - baseline['ndcg_at_k']) < 1e-10
            trials.append({'continuation_weight': weight, 'metrics': metrics})
            print(f'FOLD {index} CONT {weight}: hits={metrics["total_hits"]} '
                  f'P={metrics["precision_at_k"]:.5f} R={metrics["recall_at_k"]:.5f} '
                  f'NDCG={metrics["ndcg_at_k"]:.5f}', flush=True)
        folds.append({'fold': index, 'source_cohort_sha256': digest, 'labels': labels,
                      'continuation_profile': profile, 'trials': trials})
    means = {}
    for weight in WEIGHTS:
        selected = [next(r['metrics'] for r in fold['trials'] if r['continuation_weight'] == weight)
                    for fold in folds]
        means[str(weight)] = {metric: sum(r[metric] for r in selected)/len(selected)
                              for metric in ('precision_at_k', 'recall_at_k', 'ndcg_at_k')}
    chosen = max(WEIGHTS, key=lambda w: (means[str(w)]['ndcg_at_k'], means[str(w)]['recall_at_k'], -w))
    baseline = means[str(0)]
    improves_all = all(means[str(chosen)][metric] > baseline[metric] + 1e-12
                       for metric in ('precision_at_k', 'recall_at_k', 'ndcg_at_k'))
    report = {'protocol': 'all distinct new items from query time through remaining session',
              'scope': 'Two chronological validation folds; no final-test labels used for selection.',
              'folds': folds, 'mean_validation_metrics': means,
              'selected_continuation_weight': chosen,
              'selected_improves_all_three_on_validation': improves_all}
    OUT.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(f'SELECTED {chosen}; ALL THREE IMPROVED {improves_all}; WROTE {OUT}', flush=True)


if __name__ == '__main__':
    run()
