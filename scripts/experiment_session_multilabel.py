"""Score all remaining new session items and select fusion on validation only."""
from __future__ import annotations

import json
from pathlib import Path

from pipeline.session_model import context_affinities
from pipeline.session_multilabel import remaining_session_labels
from pipeline.session_transition import fit_transitions, fused_rank
from pipeline.session_upgrade import CURRENT, EventData, SparseGraph, summarize
from scripts.experiment_session_coverage import load_clean_csv


ROOT = Path(__file__).resolve().parents[1]
METRICS = ROOT / 'outputs/runs/session-20261022-1327/metrics'
OUT = ROOT / 'outputs/evidence/session_multilabel_validation.json'
WEIGHTS = (0, .1, .2, .25, .3, .4, .5)


def run():
    count, arrays = load_clean_csv()
    assert count == 2756101 and len(arrays['timestamp']) == 2755641
    data = EventData(arrays)
    split = json.loads((METRICS / 'temporal_split.json').read_text())
    folds = []
    for index, (cutoff, end) in enumerate(zip(split['boundaries'], split['boundaries'][1:]), 1):
        rows, population = data.session_rows(cutoff, end)
        original = json.loads((METRICS / f'session_validation_fold_{index}.json').read_text())
        from pipeline.session_upgrade import canonical_hash
        assert canonical_hash(rows) == original['cohort_sha256']
        label_profile = remaining_session_labels(data, rows, end)
        pop = data.popularity(cutoff)
        seeds = {seed for row in rows for seed, _ in context_affinities(row['context'], CURRENT, row['as_of'])}
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
            trials.append({'transition_weight': weight, 'metrics': metrics})
            print(f'FOLD {index} WEIGHT {weight}: hits={metrics["total_hits"]} '
                  f'P={metrics["precision_at_k"]:.5f} R={metrics["recall_at_k"]:.5f} '
                  f'NDCG={metrics["ndcg_at_k"]:.5f}', flush=True)
        folds.append({'fold': index, 'source_cohort_sha256': original['cohort_sha256'],
                      'population': population, 'labels': label_profile,
                      'transition_profile': profile, 'trials': trials})
    means = {}
    for weight in WEIGHTS:
        selected = [next(t['metrics'] for t in fold['trials'] if t['transition_weight'] == weight) for fold in folds]
        means[str(weight)] = {key: sum(t[key] for t in selected)/len(selected)
                              for key in ('precision_at_k', 'recall_at_k', 'ndcg_at_k')}
    chosen = max(WEIGHTS, key=lambda w: (means[str(w)]['ndcg_at_k'], means[str(w)]['recall_at_k'], -abs(w-.25)))
    current = means[str(.25)]
    improves_all = all(means[str(chosen)][key] > current[key] + 1e-12
                       for key in ('precision_at_k', 'recall_at_k', 'ndcg_at_k'))
    report = {'protocol': 'all distinct new items from query time through end of same session, capped at evaluation period end',
              'scope': 'Frozen 5,000-query cohort per chronological validation fold; no test inspection.',
              'folds': folds, 'mean_validation_metrics': means,
              'current_transition_weight': .25, 'selected_transition_weight': chosen,
              'selected_improves_all_three_on_validation': improves_all}
    OUT.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(f'SELECTED {chosen}; ALL THREE IMPROVED {improves_all}; WROTE {OUT}', flush=True)


if __name__ == '__main__':
    run()
