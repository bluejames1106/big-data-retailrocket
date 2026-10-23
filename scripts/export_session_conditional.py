"""Export the validated conditional session graph as a separate demo snapshot.

This does not activate the snapshot; verify it through a pinned API first.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import shutil

from pipeline.config import atomic_json
from pipeline.session_upgrade import EventData, SparseGraph
from scripts.experiment_session_coverage import load_clean_csv


ROOT = Path(__file__).resolve().parents[1]
OLD = ROOT / "outputs/runs/session-20261022-1327"
NEW = ROOT / "outputs/runs/conditional-20261022-v1"


def run():
    validation = json.loads((ROOT / "outputs/evidence/session_similarity_experiment.json").read_text())
    heldout = json.loads((ROOT / "outputs/evidence/session_conditional_heldout.json").read_text())
    for fold in validation["folds"]:
        scores = {(r["similarity"], r["shrinkage"]): r["metrics"] for r in fold["trials"]}
        for metric in ("precision_at_k", "recall_at_k", "ndcg_at_k"):
            assert scores[("conditional", 3)][metric] > scores[("jaccard", 3)][metric]
    for metric in ("precision_at_k", "recall_at_k", "ndcg_at_k"):
        assert heldout["candidate"][metric] > heldout["baseline"][metric]
    if NEW.exists():
        raise FileExistsError(NEW)
    serving = NEW / "serving"
    serving.mkdir(parents=True)
    _, arrays = load_clean_csv()
    data = EventData(arrays)
    graph = SparseGraph(data, int(data.a["timestamp"].max()), "binary_user",
                        binary_similarity="conditional", shrinkage=3)
    profile = graph.export(serving / "session_neighbors.sqlite")
    shutil.copy2(OLD / "serving/seen_items.sqlite", serving / "seen_items.sqlite")
    original = json.loads((OLD / "serving/model_bundle.json").read_text())
    version = NEW.name + ":session-serving-v1"
    now = datetime.now(timezone.utc).isoformat()
    bundle = {**original, "run_id": NEW.name, "model_version": version, "trained_at_utc": now}
    bundle["session_model"] = {
        "model_version": version + ":session", "metric_model_version": NEW.name + ":session-evaluation-v1",
        "index_file": "session_neighbors.sqlite", "reference_run_id": OLD.name,
        "configuration": {"name": "conditional_user", "graph": "binary_user", "binary_similarity": "conditional",
                          "shrinkage": 3, "event_weights": {"view": 1, "addtocart": 1, "transaction": 1},
                          "query_half_life_minutes": None, "position_decay": 0.8, "seed_limit": 5,
                          "popularity_weight": 0},
        "metrics": {"protocol": "session next-new-item conditional query; one target per eligible session", "k": 10,
                    "current_item_item": heldout["baseline"], "selected": heldout["candidate"],
                    "popularity": original["session_model"]["metrics"]["popularity"],
                    "selected_configuration": {"name": "conditional_user", "graph": "binary_user",
                                               "binary_similarity": "conditional", "shrinkage": 3},
                    "served_policy": "conditional_user", "selected_model_promoted": True,
                    "test_reuse_disclosure": heldout["promotion_note"],
                    "paired_visitor_cluster_bootstrap_95": heldout["paired_visitor_cluster_bootstrap_95"]},
        "index_profile": profile,
    }
    atomic_json(serving / "model_bundle.json", bundle)
    metrics = NEW / "metrics"
    metrics.mkdir()
    shutil.copy2(OLD / "metrics/kpis.json", metrics / "kpis.json")
    atomic_json(metrics / "run_report.json", {"run_id": NEW.name, "source_run_id": OLD.name,
                "source_data_run_id": "full-20261022-1031", "status": "completed_not_activated",
                "completed_at_utc": now, "scope": "CSV reconstructed full-history serving graph; unchanged static bundle",
                "test_reuse_disclosure": heldout["promotion_note"]})
    atomic_json(metrics / "session_validation_selection.json", validation)
    atomic_json(metrics / "session_heldout_comparison.json", heldout)
    print(json.dumps({"run_id": NEW.name, "model_version": version, "index_profile": profile}, indent=2), flush=True)


if __name__ == "__main__":
    run()
