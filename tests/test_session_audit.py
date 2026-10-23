"""Regression tests for the independent, protocol-separated session audit."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from pipeline.ranking import user_ranking_metrics
from scripts.verify_session_upgrade import canonical_hash, verify, verify_rows


def row(query="q1", visitor=1, target=20, *, correct=True):
    return {"query_id": query, "visitorid": visitor, "actual": [target], "seen": [10],
            "context": [{"itemid": 10, "event": "view", "timestamp": 100}],
            "as_of": 100, "target_timestamp": 101,
            "selected": [target] if correct else [30],
            "current_item_item": [30], "popularity": [40]}


def report(rows):
    models = {}
    for name in ("selected", "current_item_item", "popularity"):
        scores = [user_ranking_metrics(value[name], value["actual"], 10) for value in rows]
        models[name] = {metric: sum(value[metric] for value in scores) / len(scores)
                        for metric in ("precision_at_k", "recall_at_k", "ndcg_at_k")}
        models[name]["total_hits"] = sum(len(set(value[name]) & set(value["actual"])) for value in rows)
        models[name]["query_count"] = len(rows)
    return {"k": 10, "protocol": "next_new_item_conditional_on_strictly_past_session_context",
            "models": models, "cohort_sha256": canonical_hash(rows),
            "graph_train_end_inclusive": 90, "rankings_file": "rankings.jsonl",
            "evaluated_at_utc": "2026-10-22T03:00:00+00:00"}


class SessionAuditTests(unittest.TestCase):
    def test_macro_denominator_keeps_misses_and_all_three_models(self):
        rows = [row(), row("q2", 2, 21, correct=False)]
        result = verify_rows(rows, report(rows), session=True)
        self.assertEqual(result["queries"], 2)
        self.assertEqual(result["models"]["selected"]["total_hits"], 1)
        self.assertEqual(result["models"]["selected"]["precision_at_k"], .05)
        self.assertEqual(result["models"]["selected"]["recall_at_k"], .5)
        self.assertEqual(result["models"]["selected"]["ndcg_at_k"], .5)
        self.assertEqual(result["models"]["popularity"]["recall_at_k"], 0.)

    def test_zero_hits_and_regression_are_valid_outcomes(self):
        rows = [row(correct=False)]
        rows[0]["current_item_item"] = [20]
        result = verify_rows(rows, report(rows), session=True)
        self.assertEqual(result["models"]["selected"]["precision_at_k"], 0.)
        self.assertLess(result["selected_vs_current"]["recall_at_k"], 0.)

    def test_canonical_hash_ignores_prediction_and_row_order_only(self):
        rows = [row("b", 2), row("a", 1)]
        canonical = [{key: value[key] for key in
                      ("query_id", "visitorid", "actual", "seen", "context", "as_of", "target_timestamp")}
                     for value in sorted(rows, key=lambda value: value["query_id"])]
        expected = hashlib.sha256(json.dumps(canonical, ensure_ascii=True, separators=(",", ":"),
                                             sort_keys=True).encode()).hexdigest()
        self.assertEqual(canonical_hash(rows), expected)
        changed = deepcopy(rows[::-1])
        changed[0]["selected"] = []
        self.assertEqual(canonical_hash(changed), expected)
        changed[0]["context"][0]["event"] = "transaction"
        self.assertNotEqual(canonical_hash(changed), expected)

    def test_future_context_and_equal_target_time_are_rejected(self):
        for field in ("context", "target"):
            with self.subTest(field=field):
                rows = [row()]
                if field == "context":
                    rows[0]["context"][0]["timestamp"] = 101
                else:
                    rows[0]["target_timestamp"] = 100
                with self.assertRaisesRegex(ValueError, "future|strictly after"):
                    verify_rows(rows, report(rows), session=True)

    def test_graph_cutoff_must_precede_inference_time(self):
        rows = [row()]
        recorded = report(rows)
        recorded["graph_train_end_inclusive"] = 102
        with self.assertRaisesRegex(ValueError, "graph training"):
            verify_rows(rows, recorded, session=True)

    def test_duplicates_seen_targets_and_missing_shared_context_rejected(self):
        cases = [("duplicate recommendation", lambda value: value.update(selected=[20, 20])),
                 ("seen recommendation", lambda value: value.update(selected=[10])),
                 ("seen target", lambda value: value.update(actual=[10])),
                 ("missing context exclusion", lambda value: value.update(seen=[]))]
        for label, mutation in cases:
            with self.subTest(label=label):
                rows = [row()]
                mutation(rows[0])
                with self.assertRaises(ValueError):
                    verify_rows(rows, report(rows), session=True)

    def test_session_requires_one_target_and_past_context(self):
        rows = [row()]
        rows[0]["actual"] = [20, 21]
        with self.assertRaisesRegex(ValueError, "exactly one"):
            verify_rows(rows, report(rows), session=True)
        rows = [row()]
        rows[0]["context"] = []
        with self.assertRaisesRegex(ValueError, "past context"):
            verify_rows(rows, report(rows), session=True)

    def test_static_macro_recall_is_not_session_recall(self):
        rows = [row(), row("q2", 2)]
        rows[0]["actual"] = [20, 21, 22, 23]
        rows[1]["selected"] = []
        result = verify_rows(rows, report(rows), session=False)
        self.assertEqual(result["models"]["selected"]["precision_at_k"], .05)
        self.assertEqual(result["models"]["selected"]["recall_at_k"], .125)
        self.assertEqual(result["relevant_pairs"], 5)

    def test_wrong_metric_or_hash_is_rejected(self):
        rows = [row()]
        recorded = report(rows)
        recorded["models"]["selected"]["recall_at_k"] = .8
        with self.assertRaisesRegex(ValueError, "recomputation"):
            verify_rows(rows, recorded, session=True)
        recorded = report(rows)
        recorded["cohort_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "hash"):
            verify_rows(rows, recorded, session=True)

    def test_query_id_collision_rejected(self):
        rows = [row(), row(visitor=2)]
        with self.assertRaisesRegex(ValueError, "Duplicate query_id"):
            verify_rows(rows, report(rows), session=True)

    def _artifacts(self, root):
        output_root = root / "outputs"
        metrics = output_root / "runs" / "session-test" / "metrics"
        metrics.mkdir(parents=True)
        plan = {"registered_at_utc": "2026-10-22T01:00:00+00:00",
                "candidate_policies": [{"name": "candidate", "graph": "weighted_user"}],
                "current_policy": {"name": "current_item_item"},
                "static_promotion": "strict_all_three", "session_promotion": "strict_all_three"}
        selection = {"frozen_at_utc": "2026-10-22T02:00:00+00:00"}
        rows = [row()]
        for label in ("static_test", "session_test"):
            protocol = label.split("_")[0]
            recorded = report(rows)
            recorded["rankings_file"] = f"{label}_rankings.jsonl"
            recorded["selected_configuration"] = {"name": "candidate", "graph": "weighted_user"}
            if label == "static_test":
                recorded["reference_run_id"] = "neighbors-test"
            (metrics / f"{label}_comparison.json").write_text(json.dumps(recorded), encoding="utf-8")
            (metrics / f"{label}_rankings.jsonl").write_text("\n".join(map(json.dumps, rows)), encoding="utf-8")
            scores = {"candidate": {"precision_at_k": .02, "recall_at_k": .2, "ndcg_at_k": .2},
                      "current_item_item": {"precision_at_k": .01, "recall_at_k": .1, "ndcg_at_k": .1},
                      "popularity": {"precision_at_k": .005, "recall_at_k": .05, "ndcg_at_k": .05}}
            selection[protocol] = {"selected_configuration": recorded["selected_configuration"],
                                   "mean_metrics": deepcopy(scores), "validation_beats_current_all_three": True}
            for index, (cutoff, end) in enumerate(((50, 60), (60, 80)), 1):
                fold = {"protocol": protocol, "fold": {"train_end_inclusive": cutoff,
                        "validation_start_exclusive": cutoff, "validation_end_inclusive": end}, "scores": scores,
                        "graphs": [{"train_end_inclusive": cutoff}]}
                (metrics / f"{protocol}_validation_fold_{index}.json").write_text(json.dumps(fold), encoding="utf-8")
            (metrics / f"{protocol}_promotion.json").write_text(json.dumps({"passed": True,
                "rule": "strict_all_three", "checked_at_utc": "2026-10-22T03:01:00+00:00"}), encoding="utf-8")
        (metrics / "experiment_plan.json").write_text(json.dumps(plan), encoding="utf-8")
        (metrics / "policy_selection.json").write_text(json.dumps(selection), encoding="utf-8")
        prior = output_root / "runs" / "neighbors-test" / "metrics"
        prior.mkdir(parents=True)
        previous = [{"visitorid": value["visitorid"], "actual": value["actual"], "seen": value["seen"],
                     "selected": value["current_item_item"]} for value in rows]
        (prior / "primary_test_rankings.jsonl").write_text("\n".join(map(json.dumps, previous)), encoding="utf-8")
        return output_root, metrics

    def test_full_audit_preserves_protocols_and_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            output_root, metrics = self._artifacts(Path(directory))
            result = verify(metrics.parent, output_root)
            self.assertEqual(result["status"], "passed")
            self.assertEqual(set(result["results"]), {"static_test", "session_test"})
            self.assertTrue(result["results"]["static_test"]["current_model_reference"]
                            ["same_users_targets_history_and_current_rankings"])
            self.assertTrue(result["validation_selection_and_promotion"]["session"]["validation_means_recomputed"])

    def test_validation_mean_tampering_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            output_root, metrics = self._artifacts(Path(directory))
            selection = json.loads((metrics / "policy_selection.json").read_text())
            selection["session"]["mean_metrics"]["candidate"]["ndcg_at_k"] = .5
            (metrics / "policy_selection.json").write_text(json.dumps(selection), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "validation mean"):
                verify(metrics.parent, output_root)

    def test_policy_changes_after_freeze_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            output_root, metrics = self._artifacts(Path(directory))
            comparison = json.loads((metrics / "session_test_comparison.json").read_text())
            comparison["selected_configuration"]["popularity_weight"] = .9
            (metrics / "session_test_comparison.json").write_text(json.dumps(comparison), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "differs from frozen"):
                verify(metrics.parent, output_root)

    def test_winner_must_follow_ndcg_then_recall_precision_name_rule(self):
        with tempfile.TemporaryDirectory() as directory:
            output_root, metrics = self._artifacts(Path(directory))
            plan = json.loads((metrics / "experiment_plan.json").read_text())
            plan["candidate_policies"].append({"name": "better", "graph": "weighted_session"})
            (metrics / "experiment_plan.json").write_text(json.dumps(plan), encoding="utf-8")
            selection = json.loads((metrics / "policy_selection.json").read_text())
            for protocol in ("static", "session"):
                for index in (1, 2):
                    path = metrics / f"{protocol}_validation_fold_{index}.json"
                    fold = json.loads(path.read_text())
                    fold["scores"]["better"] = {"precision_at_k": .03, "recall_at_k": .3, "ndcg_at_k": .3}
                    path.write_text(json.dumps(fold), encoding="utf-8")
                selection[protocol]["mean_metrics"]["better"] = {"precision_at_k": .03, "recall_at_k": .3, "ndcg_at_k": .3}
            (metrics / "policy_selection.json").write_text(json.dumps(selection), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "frozen winner"):
                verify(metrics.parent, output_root)

    def test_promotion_requires_improvement_on_every_validation_metric(self):
        with tempfile.TemporaryDirectory() as directory:
            output_root, metrics = self._artifacts(Path(directory))
            selection = json.loads((metrics / "policy_selection.json").read_text())
            for index in (1, 2):
                path = metrics / f"session_validation_fold_{index}.json"
                fold = json.loads(path.read_text())
                fold["scores"]["candidate"]["recall_at_k"] = .1
                path.write_text(json.dumps(fold), encoding="utf-8")
            selection["session"]["mean_metrics"]["candidate"]["recall_at_k"] = .1
            selection["session"]["validation_beats_current_all_three"] = False
            (metrics / "policy_selection.json").write_text(json.dumps(selection), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "promotion gate"):
                verify(metrics.parent, output_root)
            promotion = json.loads((metrics / "session_promotion.json").read_text())
            promotion["passed"] = False
            (metrics / "session_promotion.json").write_text(json.dumps(promotion), encoding="utf-8")
            result = verify(metrics.parent, output_root)
            self.assertFalse(result["validation_selection_and_promotion"]["session"]["promotion_gate_passed"])

    def test_validation_cannot_use_later_than_final_training_period(self):
        with tempfile.TemporaryDirectory() as directory:
            output_root, metrics = self._artifacts(Path(directory))
            path = metrics / "session_validation_fold_2.json"
            fold = json.loads(path.read_text())
            fold["fold"]["validation_end_inclusive"] = 95
            path.write_text(json.dumps(fold), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "last validation boundary"):
                verify(metrics.parent, output_root)

    def test_static_reference_changes_cannot_hide_behind_new_cohort_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            output_root, metrics = self._artifacts(Path(directory))
            rows = [row()]
            rows[0]["current_item_item"] = [20]
            recorded = report(rows)
            recorded.update(reference_run_id="neighbors-test", rankings_file="static_test_rankings.jsonl")
            (metrics / "static_test_comparison.json").write_text(json.dumps(recorded), encoding="utf-8")
            (metrics / "static_test_rankings.jsonl").write_text(json.dumps(rows[0]), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Current static recommendations changed"):
                verify(metrics.parent, output_root)

    def test_late_plan_and_early_evaluation_rejected(self):
        for path, value in (("experiment_plan.json", {"registered_at_utc": "2026-10-22T04:00:00+00:00"}),
                            ("policy_selection.json", {"frozen_at_utc": "2026-10-22T04:00:00+00:00"})):
            with self.subTest(path=path), tempfile.TemporaryDirectory() as directory:
                output_root, metrics = self._artifacts(Path(directory))
                (metrics / path).write_text(json.dumps(value), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "after policy|precedes frozen"):
                    verify(metrics.parent, output_root)


if __name__ == "__main__":
    unittest.main()
