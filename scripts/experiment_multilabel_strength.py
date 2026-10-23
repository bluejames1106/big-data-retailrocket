"""Compare normalized edge-strength fusion with served reciprocal-rank fusion."""
from __future__ import annotations

import json
from pathlib import Path

from pipeline.session_model import context_affinities
from pipeline.session_multilabel import remaining_session_labels
from pipeline.session_transition import fit_transitions, fused_rank, strength_fused_rank
from pipeline.session_upgrade import CURRENT, EventData, SparseGraph, canonical_hash, summarize
from scripts.experiment_session_coverage import load_clean_csv


ROOT = Path(__file__).resolve().parents[1]
METRICS = ROOT / 'outputs/runs/session-20261022-1327/metrics'
OUT = ROOT / 'outputs/evidence/multilabel_strength_validation.json'
TRIALS = [(w, p) for w in (.1, .25, .4, .6) for p in (.5, 1, 2)]


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
        for name, transition_weight, power in [('served_rrf', None, None)] + [
                (f'strength_{weight}_{exponent}', weight, exponent) for weight, exponent in TRIALS]:
            predictions = []
            for row in rows:
                result = (fused_rank(co, trans, row['context'], pop, row['seen'], .25, 10, CURRENT, row['as_of'])
                          if name == 'served_rrf' else
                          strength_fused_rank(co, trans, row['context'], pop, row['seen'],
                                              transition_weight, power, 10, CURRENT, row['as_of']))
                predictions.append({**row, 'trial': [r['itemid'] for r in result['items']]})
            metrics = summarize(predictions, 'trial')
            if name == 'served_rrf':
                baseline = next(r['metrics'] for r in previous['folds'][index-1]['trials']
                                if r['transition_weight'] == .25)
                assert metrics['total_hits'] == baseline['total_hits']
                assert abs(metrics['ndcg_at_k'] - baseline['ndcg_at_k']) < 1e-10
            trials.append({'name': name, 'transition_weight': transition_weight, 'power': power, 'metrics': metrics})
            print(f'FOLD {index} {name}: hits={metrics["total_hits"]} '
                  f'P={metrics["precision_at_k"]:.5f} R={metrics["recall_at_k"]:.5f} '
                  f'NDCG={metrics["ndcg_at_k"]:.5f}', flush=True)
        folds.append({'fold': index, 'trials': trials})
    names = [r['name'] for r in folds[0]['trials']]
    means = {}
    for name in names:
        values = [next(r['metrics'] for r in fold['trials'] if r['name'] == name) for fold in folds]
        means[name] = {metric: sum(r[metric] for r in values)/len(values)
                       for metric in ('precision_at_k', 'recall_at_k', 'ndcg_at_k')}
    selected = max(names, key=lambda name: (means[name]['ndcg_at_k'], means[name]['recall_at_k'], name))
    improves_all = all(means[selected][metric] > means['served_rrf'][metric] + 1e-12
                       for metric in ('precision_at_k', 'recall_at_k', 'ndcg_at_k'))
    report = {'scope': 'Two frozen chronological multi-label validation folds only.', 'folds': folds,
              'mean_validation_metrics': means, 'selected': selected,
              'selected_improves_all_three_on_validation': improves_all}
    OUT.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(f'SELECTED {selected}; ALL THREE IMPROVED {improves_all}; WROTE {OUT}', flush=True)


if __name__ == '__main__':
    run()
