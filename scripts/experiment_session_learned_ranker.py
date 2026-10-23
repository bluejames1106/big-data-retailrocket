"""Train a small candidate ranker on validation fold 1 and score fold 2.

This is an experiment only. The previously viewed final test is never read.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from scipy.optimize import minimize

from pipeline.session_model import context_affinities
from pipeline.session_multilabel import remaining_session_labels
from pipeline.session_transition import fit_transitions, strength_fused_rank
from pipeline.session_upgrade import CURRENT, EventData, SparseGraph, canonical_hash, summarize
from scripts.experiment_session_coverage import load_clean_csv

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/evidence/session_learned_ranker_validation.json'
SPLIT = ROOT / 'outputs/runs/session-20261022-1327/metrics/temporal_split.json'


def features(row, co, trans, popularity):
    """Features from training-only graphs and query context."""
    excluded = set(row['seen']) | {int(e['itemid']) for e in row['context']}
    seeds = context_affinities(row['context'], CURRENT, row['as_of'])
    values = {}
    for graph, offset in ((co, 0), (trans, 3)):
        for seed, affinity in seeds:
            edges = graph.get(seed, ())
            if not edges:
                continue
            maximum = max(float(edges[0][1]), 1e-12)
            for rank, (candidate, strength) in enumerate(edges, 1):
                if candidate in excluded or strength <= 0:
                    continue
                vector = values.setdefault(candidate, np.zeros(9, dtype=np.float32))
                vector[offset] += affinity * (float(strength) / maximum) ** .5
                vector[offset + 1] = max(vector[offset + 1], affinity / (rank + 5))
                vector[offset + 2] += 1
    for rank, entry in enumerate(popularity[:100], 1):
        item = int(entry['itemid'] if isinstance(entry, dict) else entry)
        if item not in excluded:
            vector = values.setdefault(item, np.zeros(9, dtype=np.float32))
            vector[6] = 1 / np.sqrt(rank)
    for vector in values.values():
        vector[2] = np.log1p(vector[2])
        vector[5] = np.log1p(vector[5])
        vector[7] = min(len(seeds), 5) / 5
        vector[8] = 1
    return values


def fold(data, cutoff, end, expected_hash):
    rows, _ = data.session_rows(cutoff, end)
    assert canonical_hash(rows) == expected_hash
    remaining_session_labels(data, rows, end)
    pop = data.popularity(cutoff)
    seeds = {seed for row in rows for seed, _ in context_affinities(row['context'], CURRENT, row['as_of'])}
    co = SparseGraph(data, cutoff, 'binary_user', binary_similarity='conditional', shrinkage=3).neighbors(seeds)
    all_trans, _ = fit_transitions(data, cutoff)
    trans = {seed: all_trans.get(seed, ()) for seed in seeds}
    matrices, labels, candidate_hits = [], [], 0
    for row in rows:
        candidates = features(row, co, trans, pop)
        ids = np.array(list(candidates), dtype=np.int32)
        matrix = np.stack(list(candidates.values()))
        relevant = np.isin(ids, row['actual'])
        candidate_hits += int(relevant.sum())
        matrices.append((ids, matrix))
        labels.append(relevant)
        baseline = strength_fused_rank(co, trans, row['context'], pop, row['seen'], .25, .5, 10, CURRENT, row['as_of'])
        row['baseline'] = [item['itemid'] for item in baseline['items']]
    return rows, matrices, labels, candidate_hits


def train(matrices, labels, penalty):
    x = np.concatenate([m for _, m in matrices]).astype(np.float64)
    y = np.concatenate(labels).astype(np.float64)
    # A query contributes similar total positive and negative weight.
    weights = np.concatenate([np.where(a, .5 / max(int(a.sum()), 1),
                                      .5 / max(int((~a).sum()), 1)) for a in labels])
    scale = np.std(x, axis=0)
    scale[scale < 1e-8] = 1
    scale[-1] = 1
    x /= scale
    def objective(coef):
        z = np.clip(x @ coef, -40, 40)
        loss = np.sum(weights * (np.logaddexp(0, z) - y * z)) + penalty * np.sum(coef[:-1] ** 2)
        grad = x.T @ (weights * (1 / (1 + np.exp(-z)) - y))
        grad[:-1] += 2 * penalty * coef[:-1]
        return loss, grad
    result = minimize(objective, np.zeros(x.shape[1]), jac=True, method='L-BFGS-B',
                      options={'maxiter': 100})
    return result.x / scale, bool(result.success)


def evaluate(rows, matrices, coefficients, blend=1):
    for row, (ids, matrix) in zip(rows, matrices):
        learned = matrix @ coefficients
        strength = .75 * matrix[:, 0] + .25 * matrix[:, 3]
        learned_order = np.lexsort((ids, -learned))
        strength_order = np.lexsort((ids, -strength))
        learned_rank = np.empty(len(ids), dtype=np.int32)
        strength_rank = np.empty(len(ids), dtype=np.int32)
        learned_rank[learned_order] = np.arange(1, len(ids) + 1)
        strength_rank[strength_order] = np.arange(1, len(ids) + 1)
        scores = blend / (60 + learned_rank) + (1 - blend) / (60 + strength_rank)
        order = np.lexsort((ids, -scores))[:10]
        row['candidate'] = ids[order].tolist()
    return summarize(rows, 'candidate')


def run():
    split = json.loads(SPLIT.read_text())
    previous = json.loads((ROOT / 'outputs/evidence/session_multilabel_validation.json').read_text())
    _, arrays = load_clean_csv()
    data = EventData(arrays)
    folds = []
    for index, (cutoff, end) in enumerate(zip(split['boundaries'], split['boundaries'][1:])):
        digest = previous['folds'][index]['source_cohort_sha256']
        folds.append(fold(data, cutoff, end, digest))
        print(f'Built fold {index+1}: {folds[-1][3]} candidate hits', flush=True)
    train_rows, train_x, train_y, train_hits = folds[0]
    valid_rows, valid_x, _, valid_hits = folds[1]
    trials = []
    for penalty in (1, 10, 100):
        coef, converged = train(train_x, train_y, penalty)
        for blend in (.1, .25, .5, 1):
            metric = evaluate(valid_rows, valid_x, coef, blend)
            trials.append({'penalty': penalty, 'blend': blend, 'converged': converged,
                           'coefficients': coef.tolist(), 'metrics': metric})
            print(f'penalty={penalty} blend={blend}: P={metric["precision_at_k"]:.5f} '
                  f'R={metric["recall_at_k"]:.5f} NDCG={metric["ndcg_at_k"]:.5f}', flush=True)
    baseline = summarize(valid_rows, 'baseline')
    winner = max(trials, key=lambda t: t['metrics']['ndcg_at_k'])
    report = {'scope': 'Train on frozen validation fold 1; select on fold 2; final test untouched.',
              'train_queries': len(train_rows), 'validation_queries': len(valid_rows),
              'train_candidate_hits': train_hits, 'validation_candidate_hits': valid_hits,
              'baseline': baseline, 'trials': trials,
              'selected': {'penalty': winner['penalty'], 'blend': winner['blend']},
              'improves_all_three': all(winner['metrics'][m] > baseline[m] + 1e-12
                                        for m in ('precision_at_k', 'recall_at_k', 'ndcg_at_k'))}
    OUT.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(f'WROTE {OUT}; improves all three: {report["improves_all_three"]}', flush=True)


if __name__ == '__main__':
    run()
