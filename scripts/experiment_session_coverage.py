"""Rebuild frozen validation cohorts from CSV and test graph coverage limits.

This exploratory run never reads final-test labels or updates the serving pointer.
It requires the image's NumPy and SciPy packages.
"""
from __future__ import annotations

from array import array
import csv
import json
from pathlib import Path

import numpy as np

from pipeline.session_model import context_affinities, rank_context
from pipeline.session_upgrade import CURRENT, EventData, SparseGraph, canonical_hash, summarize


ROOT = Path(__file__).resolve().parents[1]
METRICS = ROOT / "outputs/runs/session-20261022-1327/metrics"
OUT = ROOT / "outputs/evidence/session_coverage_experiment.json"
EVENTS = {"view": 0, "addtocart": 1, "transaction": 2}


def load_clean_csv():
    columns = {key: array(code) for key, code in (("timestamp", "q"), ("visitorid", "i"),
                                                  ("itemid", "i"), ("event", "b"), ("transactionid", "i"))}
    with (ROOT / "data/raw/events.csv").open(newline="", encoding="utf-8-sig") as source:
        for row in csv.DictReader(source):
            columns["timestamp"].append(int(row["timestamp"]))
            columns["visitorid"].append(int(row["visitorid"]))
            columns["itemid"].append(int(row["itemid"]))
            columns["event"].append(EVENTS[row["event"]])
            columns["transactionid"].append(int(row["transactionid"]) if row["transactionid"] else -1)
    raw = {key: np.frombuffer(value, dtype={"q": np.int64, "i": np.int32, "b": np.int8}[value.typecode])
           for key, value in columns.items()}
    records = np.rec.fromarrays(list(raw.values()), names=','.join(raw))
    _, unique_indices = np.unique(records, return_index=True)
    result = {key: values[unique_indices].copy() for key, values in raw.items() if key != "transactionid"}
    return len(records), result


def run():
    raw_count, arrays = load_clean_csv()
    assert raw_count == 2756101, raw_count
    assert len(arrays["timestamp"]) == 2755641, len(arrays["timestamp"])
    data = EventData(arrays)
    split = json.loads((METRICS / "temporal_split.json").read_text())
    folds = []
    for index, (cutoff, end) in enumerate(zip(split["boundaries"], split["boundaries"][1:]), 1):
        rows, population = data.session_rows(cutoff, end)
        recorded = json.loads((METRICS / f"session_validation_fold_{index}.json").read_text())
        digest = canonical_hash(rows)
        assert digest == recorded["cohort_sha256"], (index, digest, recorded["cohort_sha256"])
        pop = data.popularity(cutoff)
        seeds = {seed for row in rows for seed, _ in context_affinities(row["context"], CURRENT, row["as_of"])}
        trials = []
        for cap, neighbor_limit in ((20, 100), (20, 200), (50, 100), (50, 200)):
            graph = SparseGraph(data, cutoff, "binary_user", cap, neighbor_limit)
            neighbors = graph.neighbors(seeds)
            candidate_hits = 0
            matched = 0
            predictions = []
            for row in rows:
                available = {item for seed, _ in context_affinities(row["context"], CURRENT, row["as_of"])
                             for item, _ in neighbors.get(seed, ())}
                candidate_hits += row["actual"][0] in available
                result = rank_context(neighbors, row["context"], pop, row["seen"], 10, CURRENT, row["as_of"])
                matched += result["matched_seeds"] > 0
                item_ids = [item["itemid"] for item in result["items"]]
                predictions.append({**row, "trial": item_ids})
            metrics = summarize(predictions, "trial")
            if (cap, neighbor_limit) == (20, 100):
                baseline = recorded["scores"]["current_item_item"]
                assert metrics["total_hits"] == baseline["total_hits"], (index, metrics, baseline)
                assert abs(metrics["ndcg_at_k"] - baseline["ndcg_at_k"]) < 1e-10
            trials.append({"cap": cap, "neighbor_limit": neighbor_limit, "candidate_recall": candidate_hits / len(rows),
                           "matched_seed_queries": matched, "metrics": metrics})
            print(f"FOLD {index} CAP {cap} NEIGHBORS {neighbor_limit}: {metrics['total_hits']} hits, "
                  f"NDCG {metrics['ndcg_at_k']:.5f}, candidate recall {candidate_hits / len(rows):.5f}", flush=True)
        folds.append({"fold": index, "cohort_sha256": digest, "population": population, "trials": trials})
    report = {"scope": "Validation folds only; CSV dedup and cohort hash matched frozen evaluation.", "folds": folds}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"WROTE {OUT}", flush=True)


if __name__ == "__main__":
    run()
