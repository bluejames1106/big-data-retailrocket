"""Compare item similarity formulas on the two frozen validation cohorts."""
from __future__ import annotations

import json
from pathlib import Path

from pipeline.session_model import context_affinities, rank_context
from pipeline.session_upgrade import CURRENT, EventData, SparseGraph, canonical_hash, summarize
from scripts.experiment_session_coverage import load_clean_csv


ROOT = Path(__file__).resolve().parents[1]
METRICS = ROOT / "outputs/runs/session-20261022-1327/metrics"
OUT = ROOT / "outputs/evidence/session_similarity_experiment.json"
TRIALS = (("jaccard", 0), ("jaccard", 1), ("jaccard", 3), ("jaccard", 10),
          ("cosine", 3), ("conditional", 3))


def run():
    raw_count, arrays = load_clean_csv()
    assert raw_count == 2756101 and len(arrays["timestamp"]) == 2755641
    data = EventData(arrays)
    split = json.loads((METRICS / "temporal_split.json").read_text())
    folds = []
    for fold, (cutoff, end) in enumerate(zip(split["boundaries"], split["boundaries"][1:]), 1):
        rows, _ = data.session_rows(cutoff, end)
        recorded = json.loads((METRICS / f"session_validation_fold_{fold}.json").read_text())
        assert canonical_hash(rows) == recorded["cohort_sha256"]
        popular = data.popularity(cutoff)
        seeds = {seed for row in rows for seed, _ in context_affinities(row["context"], CURRENT, row["as_of"])}
        trials = []
        for similarity, shrinkage in TRIALS:
            graph = SparseGraph(data, cutoff, "binary_user", binary_similarity=similarity, shrinkage=shrinkage)
            neighbors = graph.neighbors(seeds)
            predictions = []
            for row in rows:
                result = rank_context(neighbors, row["context"], popular, row["seen"], 10, CURRENT, row["as_of"])
                predictions.append({**row, "trial": [item["itemid"] for item in result["items"]]})
            metrics = summarize(predictions, "trial")
            if (similarity, shrinkage) == ("jaccard", 3):
                baseline = recorded["scores"]["current_item_item"]
                assert metrics["total_hits"] == baseline["total_hits"]
                assert abs(metrics["ndcg_at_k"] - baseline["ndcg_at_k"]) < 1e-10
            trials.append({"similarity": similarity, "shrinkage": shrinkage, "metrics": metrics})
            print(f"FOLD {fold} {similarity}/{shrinkage}: hits={metrics['total_hits']} "
                  f"NDCG={metrics['ndcg_at_k']:.5f}", flush=True)
        folds.append({"fold": fold, "cohort_sha256": canonical_hash(rows), "trials": trials})
    report = {"scope": "Validation folds only; current serving model unchanged.", "folds": folds}
    OUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"WROTE {OUT}", flush=True)


if __name__ == "__main__":
    run()
