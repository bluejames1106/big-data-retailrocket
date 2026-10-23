"""One held-out check of the validation-selected conditional co-occurrence model."""
from __future__ import annotations

import json
from pathlib import Path

from pipeline.session_model import context_affinities, rank_context
from pipeline.session_upgrade import CURRENT, EventData, SparseGraph, canonical_hash, clustered_bootstrap, summarize
from scripts.experiment_session_coverage import load_clean_csv


ROOT = Path(__file__).resolve().parents[1]
METRICS = ROOT / "outputs/runs/session-20261022-1327/metrics"
OUT = ROOT / "outputs/evidence/session_conditional_heldout.json"


def run():
    validation = json.loads((ROOT / "outputs/evidence/session_similarity_experiment.json").read_text())
    for fold in validation["folds"]:
        scores = {(r["similarity"], r["shrinkage"]): r["metrics"] for r in fold["trials"]}
        assert scores[("conditional", 3)]["ndcg_at_k"] > scores[("jaccard", 3)]["ndcg_at_k"]
        assert scores[("conditional", 3)]["recall_at_k"] > scores[("jaccard", 3)]["recall_at_k"]
    _, arrays = load_clean_csv()
    data = EventData(arrays)
    split = json.loads((METRICS / "temporal_split.json").read_text())
    cutoff = split["boundaries"][-1]
    rows, population = data.session_rows(cutoff, int(data.a["timestamp"].max()))
    recorded = json.loads((METRICS / "session_test_comparison.json").read_text())
    assert canonical_hash(rows) == recorded["cohort_sha256"]
    previous = {r["query_id"]: r for r in map(json.loads, (METRICS / "session_test_rankings.jsonl").open())}
    assert set(previous) == {r["query_id"] for r in rows}
    popular = data.popularity(cutoff)
    seeds = {seed for row in rows for seed, _ in context_affinities(row["context"], CURRENT, row["as_of"])}
    graph = SparseGraph(data, cutoff, "binary_user", binary_similarity="conditional", shrinkage=3)
    neighbors = graph.neighbors(seeds)
    for row in rows:
        assert row["actual"] == previous[row["query_id"]]["actual"]
        row["current_item_item"] = previous[row["query_id"]]["current_item_item"]
        result = rank_context(neighbors, row["context"], popular, row["seen"], 10, CURRENT, row["as_of"])
        row["selected"] = [item["itemid"] for item in result["items"]]
    baseline = summarize(rows, "current_item_item")
    assert baseline["total_hits"] == recorded["models"]["current_item_item"]["total_hits"]
    candidate = summarize(rows, "selected")
    report = {"scope": "Previously viewed final time window; validation-selected conditional/3 evaluated once.",
              "cohort_sha256": canonical_hash(rows), "population": population,
              "baseline": baseline, "candidate": candidate,
              "paired_visitor_cluster_bootstrap_95": clustered_bootstrap(rows),
              "promotion_note": "This is not a fresh future test. Do not claim a production quality gain without later-time evidence."}
    OUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    run()
