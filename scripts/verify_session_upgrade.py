"""Independently recompute static and session ranking reports from their query logs.

The two protocols retain separate denominators. This auditor accepts improvements,
regressions, and zero hits; it verifies the evidence rather than a desired score.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.ranking import user_ranking_metrics


MODEL_NAMES = ("selected", "current_item_item", "popularity")
METRIC_NAMES = ("precision_at_k", "recall_at_k", "ndcg_at_k")
COHORT_FIELDS = ("query_id", "visitorid", "actual", "seen", "context", "as_of", "target_timestamp")
EVENT_NAMES = {"view", "addtocart", "transaction"}
RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def canonical_hash(rows: list[dict]) -> str:
    """Hash only query identities/labels/history, independently of recommendations."""
    canonical = []
    for row in sorted(rows, key=lambda value: value["query_id"]):
        value = {field: row[field] for field in COHORT_FIELDS}
        value["actual"] = sorted(value["actual"])
        value["seen"] = sorted(value["seen"])
        canonical.append(value)
    payload = json.dumps(canonical, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _integer(value, label: str) -> int:
    _check(type(value) is int and value >= 0, f"{label} must be a nonnegative integer")
    return value


def _items(value, label: str) -> list[int]:
    _check(isinstance(value, list), f"{label} must be a list")
    for item in value:
        _integer(item, label)
    _check(len(value) == len(set(value)), f"{label} contains duplicate item IDs")
    return value


def _timestamp(value: str, label: str) -> datetime:
    _check(isinstance(value, str), f"{label} must be an ISO timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    _check(parsed.tzinfo is not None and parsed.utcoffset() is not None,
           f"{label} must contain an explicit time zone")
    return parsed


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _read_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _basename(value, label: str) -> str:
    _check(isinstance(value, str) and value not in {"", ".", ".."}
           and Path(value).name == value and "/" not in value and "\\" not in value,
           f"{label} must be a file basename")
    return value


def verify_rows(rows: list[dict], recorded: dict, *, session: bool) -> dict:
    """Recompute all scores on the same complete list of query rows."""
    k = recorded.get("k")
    _check(type(k) is int and k == 10, "This registered study requires k=10")
    _check(isinstance(recorded.get("protocol"), str) and bool(recorded["protocol"].strip()),
           "A nonempty protocol description is required")
    _check(isinstance(rows, list) and bool(rows), "The query cohort must be nonempty")
    _check(set(MODEL_NAMES) <= set(recorded.get("models", {})), "All three model reports are required")
    query_ids = set()
    users = set()
    total_relevant = 0
    graph_cutoff = recorded.get("graph_train_end_inclusive")
    if graph_cutoff is not None:
        _integer(graph_cutoff, "graph_train_end_inclusive")
    for row in rows:
        _check(isinstance(row, dict) and all(field in row for field in COHORT_FIELDS),
               "Every ranking row must contain the cohort fields")
        query_id = row["query_id"]
        _check(isinstance(query_id, str) and bool(query_id), "query_id must be a nonempty string")
        _check(query_id not in query_ids, f"Duplicate query_id: {query_id}")
        query_ids.add(query_id)
        users.add(_integer(row["visitorid"], f"{query_id}.visitorid"))
        actual = set(_items(row["actual"], f"{query_id}.actual"))
        seen = set(_items(row["seen"], f"{query_id}.seen"))
        _check(bool(actual), f"{query_id}: relevant targets must not be empty")
        _check(not actual & seen, f"{query_id}: targets overlap the exclusion set")
        if session:
            _check(len(actual) == 1, f"{query_id}: session next-item queries require exactly one target")
        total_relevant += len(actual)
        as_of = _integer(row["as_of"], f"{query_id}.as_of")
        target_time = _integer(row["target_timestamp"], f"{query_id}.target_timestamp")
        _check(as_of < target_time, f"{query_id}: target must occur strictly after as_of")
        if graph_cutoff is not None:
            _check(graph_cutoff <= as_of, f"{query_id}: graph training reaches beyond query time")
        context = row["context"]
        _check(isinstance(context, list), f"{query_id}.context must be a list")
        if session:
            _check(bool(context), f"{query_id}: conditional session cohort needs past context")
        for event in context:
            _check(isinstance(event, dict), f"{query_id}: context events must be objects")
            item = _integer(event.get("itemid"), f"{query_id}.context.itemid")
            event_time = _integer(event.get("timestamp"), f"{query_id}.context.timestamp")
            _check(event.get("event") in EVENT_NAMES, f"{query_id}: unknown context event type")
            _check(event_time <= as_of, f"{query_id}: future context event leaked into query")
            _check(item in seen, f"{query_id}: context item missing from shared exclusion set")
        for name in MODEL_NAMES:
            predictions = _items(row.get(name), f"{query_id}.{name}")
            _check(len(predictions) <= k, f"{query_id}.{name}: more than k recommendations")
            _check(not set(predictions) & seen, f"{query_id}.{name}: seen recommendation leaked")
    signature = canonical_hash(rows)
    _check(signature == recorded.get("cohort_sha256"), "Cohort hash differs from the report")
    recomputed = {}
    for name in MODEL_NAMES:
        values = [user_ranking_metrics(row[name], row["actual"], k) for row in rows]
        result = {metric: sum(value[metric] for value in values) / len(rows) for metric in METRIC_NAMES}
        hits = [len(set(row[name]) & set(row["actual"])) for row in rows]
        result["total_hits"] = sum(hits)
        result["hit_queries"] = sum(value > 0 for value in hits)
        result["queries"] = len(rows)
        for metric in METRIC_NAMES:
            value = recorded["models"][name].get(metric)
            _check(type(value) in {int, float} and math.isfinite(value)
                   and abs(float(value) - result[metric]) <= 1e-12,
                   f"{name}.{metric}: recorded metric differs from query-log recomputation")
        for metric in ("total_hits", "hit_queries", "queries", "query_count"):
            if metric in recorded["models"][name]:
                expected = len(rows) if metric == "query_count" else result[metric]
                _check(recorded["models"][name][metric] == expected, f"{name}.{metric} differs")
        recomputed[name] = result
    return {"queries": len(rows), "distinct_visitors": len(users), "relevant_pairs": total_relevant,
            "cohort_sha256": signature, "protocol": recorded["protocol"], "k": k,
            "temporal_context_valid": True, "identical_exclusion_set_for_all_models": True,
            "models": recomputed,
            "selected_vs_current": {metric: recomputed["selected"][metric] - recomputed["current_item_item"][metric]
                                    for metric in METRIC_NAMES}}


def verify_reference(rows: list[dict], recorded: dict, output_root: Path) -> dict:
    """Bind static users, labels, history, and current predictions to the prior run."""
    reference_run = recorded.get("reference_run_id")
    _check(isinstance(reference_run, str) and RUN_ID.fullmatch(reference_run) is not None,
           "Static report must name a valid reference_run_id")
    filename = _basename(recorded.get("reference_rankings_file", "primary_test_rankings.jsonl"),
                         "reference_rankings_file")
    prior = _read_rows(output_root / "runs" / reference_run / "metrics" / filename)
    previous = {row["visitorid"]: row for row in prior}
    current = {row["visitorid"]: row for row in rows}
    _check(len(previous) == len(prior) and len(current) == len(rows),
           "Static reference must contain one query per visitor")
    _check(set(previous) == set(current), "Static visitor cohort differs from the frozen current model")
    for visitor, row in current.items():
        before = previous[visitor]
        _check(set(before["actual"]) == set(row["actual"]), f"Static targets changed for visitor {visitor}")
        _check(set(before["seen"]) == set(row["seen"]), f"Static exclusions changed for visitor {visitor}")
        _check(before["selected"] == row["current_item_item"],
               f"Current static recommendations changed for visitor {visitor}")
    return {"reference_run_id": reference_run, "reference_rankings_file": filename,
            "same_users_targets_history_and_current_rankings": True}


def verify_selection(root: Path, plan: dict, selection: dict, comparisons: dict) -> dict:
    """Check the registered validation winner and the strict all-three promotion gate."""
    declared = plan.get("candidate_policies")
    _check(isinstance(declared, list) and bool(declared), "Registered candidate_policies are required")
    _check(all(isinstance(policy, dict) and isinstance(policy.get("name"), str)
               and policy["name"] for policy in declared), "Every registered candidate needs a name")
    candidates = {policy["name"]: policy for policy in declared}
    _check(len(candidates) == len(declared), "Registered candidate names must be unique")
    _check(plan.get("current_policy", {}).get("name") == "current_item_item",
           "The fixed current_item_item validation baseline is required")
    names = set(candidates) | {"current_item_item", "popularity"}
    results = {}
    for protocol in ("static", "session"):
        chosen = selection.get(protocol, {})
        config = chosen.get("selected_configuration", {})
        _check(config.get("name") in candidates, f"{protocol}: frozen configuration was not registered")
        for key, value in candidates[config["name"]].items():
            _check(config.get(key) == value, f"{protocol}: selected {key} differs from registered policy")
        folds = [_read_json(root / "metrics" / f"{protocol}_validation_fold_{index}.json") for index in (1, 2)]
        prior_end = None
        for index, evidence in enumerate(folds, 1):
            _check(evidence.get("protocol") == protocol, f"{protocol}: validation fold protocol differs")
            fold = evidence.get("fold", {})
            cutoff = _integer(fold.get("train_end_inclusive"), f"{protocol}.fold{index}.train_end")
            start = _integer(fold.get("validation_start_exclusive"), f"{protocol}.fold{index}.validation_start")
            end = _integer(fold.get("validation_end_inclusive"), f"{protocol}.fold{index}.validation_end")
            _check(cutoff == start and cutoff < end, f"{protocol}: invalid chronological validation boundary")
            if prior_end is not None:
                _check(cutoff == prior_end, f"{protocol}: validation folds are not consecutive")
            prior_end = end
            scores = evidence.get("scores", {})
            _check(set(scores) == names, f"{protocol}: validation score policies differ from registered policies")
            for name in names:
                for metric in METRIC_NAMES:
                    value = scores[name].get(metric)
                    _check(type(value) in {int, float} and math.isfinite(value) and 0 <= value <= 1,
                           f"{protocol}.{name}.{metric}: invalid validation score")
            for graph in evidence.get("graphs", []):
                _check(graph.get("train_end_inclusive") == cutoff,
                       f"{protocol}: validation graph cutoff differs from its training boundary")
        recorded_means = chosen.get("mean_metrics", {})
        _check(set(recorded_means) == names, f"{protocol}: mean-metric policies differ")
        means = {name: {metric: sum(fold["scores"][name][metric] for fold in folds) / len(folds)
                        for metric in METRIC_NAMES} for name in names}
        for name in names:
            for metric in METRIC_NAMES:
                value = recorded_means[name].get(metric)
                _check(type(value) in {int, float} and math.isfinite(value)
                       and abs(value - means[name][metric]) <= 1e-12,
                       f"{protocol}.{name}.{metric}: validation mean differs from fold scores")
        winner = max(candidates, key=lambda name: (means[name]["ndcg_at_k"], means[name]["recall_at_k"],
                                                  means[name]["precision_at_k"], name))
        _check(config["name"] == winner, f"{protocol}: frozen winner differs from registered validation rule")
        validation_pass = all(means[winner][metric] > means["current_item_item"][metric] + 1e-12 for metric in METRIC_NAMES)
        _check(chosen.get("validation_beats_current_all_three") is validation_pass,
               f"{protocol}: recorded all-three validation gate differs")
        comparison = comparisons[protocol + "_test"]
        _check(comparison.get("selected_configuration") == config,
               f"{protocol}: final-test configuration differs from frozen selection")
        graph_cutoff = comparison.get("graph_train_end_inclusive")
        _check(type(graph_cutoff) is int and graph_cutoff >= prior_end,
               f"{protocol}: final training cutoff precedes the last validation boundary")
        test_pass = all(comparison["models"]["selected"][metric] > comparison["models"]["current_item_item"][metric] + 1e-12
                        for metric in METRIC_NAMES)
        promotion = _read_json(root / "metrics" / f"{protocol}_promotion.json")
        expected = validation_pass and test_pass
        _check(promotion.get("passed") is expected, f"{protocol}: recorded promotion gate differs")
        _check(promotion.get("rule") == plan.get(protocol + "_promotion"),
               f"{protocol}: promotion rule differs from registered plan")
        _check(_timestamp(promotion.get("checked_at_utc"), f"{protocol}.checked_at_utc") >=
               _timestamp(comparison["evaluated_at_utc"], f"{protocol}.evaluated_at_utc"),
               f"{protocol}: promotion was recorded before test evaluation")
        results[protocol] = {"selected_policy": winner, "validation_means_recomputed": True,
                             "registered_ndcg_recall_precision_name_winner_verified": True,
                             "validation_beats_current_all_three": validation_pass,
                             "test_beats_current_all_three": test_pass, "promotion_gate_passed": expected}
    return results


def verify(root: Path, output_root: Path | None = None) -> dict:
    root = Path(root)
    output_root = Path(output_root) if output_root is not None else root.parent.parent
    plan = _read_json(root / "metrics" / "experiment_plan.json")
    selection = _read_json(root / "metrics" / "policy_selection.json")
    registered = _timestamp(plan.get("registered_at_utc"), "registered_at_utc")
    frozen_value = selection.get("frozen_at_utc", selection.get("selected_at_utc"))
    frozen = _timestamp(frozen_value, "frozen_at_utc")
    _check(registered <= frozen, "Experiment plan was registered after policy selection")
    results = {}
    comparisons = {}
    for label, is_session in (("static_test", False), ("session_test", True)):
        recorded = _read_json(root / "metrics" / f"{label}_comparison.json")
        evaluated = _timestamp(recorded.get("evaluated_at_utc"), f"{label}.evaluated_at_utc")
        _check(frozen <= evaluated, f"{label}: test evaluation precedes frozen policy selection")
        filename = _basename(recorded.get("rankings_file"), "rankings_file")
        rows = _read_rows(root / "metrics" / filename)
        result = verify_rows(rows, recorded, session=is_session)
        if not is_session:
            result["current_model_reference"] = verify_reference(rows, recorded, output_root)
        result["recorded_selection_precedes_evaluation"] = True
        result["evaluated_at_utc"] = recorded["evaluated_at_utc"]
        results[label] = result
        comparisons[label] = recorded
    validation = verify_selection(root, plan, selection, comparisons)
    return {"status": "passed", "run_id": root.name,
            "static_and_session_protocols_reported_separately": True,
            "recorded_plan_registration_precedes_selection": True,
            "no_improvement_required_for_audit_pass": True,
            "note": "Artifact ordering and ranking logs are verified; timestamp ordering alone does not prove absence of test-driven manual tuning.",
            "validation_selection_and_promotion": validation, "results": results}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output-root", default="outputs")
    args = parser.parse_args()
    _check(RUN_ID.fullmatch(args.run_id) is not None, "Invalid run ID")
    output_root = Path(args.output_root)
    root = output_root / "runs" / args.run_id
    result = verify(root, output_root)
    (root / "metrics" / "independent_session_audit.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
