"""Session HTTP contract tested against the real shared scorer and SQLite graph."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest

from fastapi.testclient import TestClient

from service.app import create_app
from service.backends import Backends
from service.store import Settings


class SessionServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="session-api-")
        self.outputs = Path(self.temporary.name)
        self.settings = Settings(output_root=self.outputs)
        self.online = Backends(self.settings)
        self.client = TestClient(create_app(self.settings, self.online))
        self.serving = self.outputs / "runs" / "run-session" / "serving"
        self.serving.mkdir(parents=True)
        self.data = {"run_id": "run-session", "model_version": "static-v2",
                     "trained_at_utc": "2026-10-22T08:00:00+00:00", "algorithm": "item_knn_hybrid",
                     "recommendations": {"7": [{"itemid": 12, "score": 1.0}]},
                     "seen_items": {"7": [1, 11]},
                     "popular_items": [{"itemid": item, "score": float(20 - index)}
                                       for index, item in enumerate([1, 2, 5, 6, 3])],
                     "metrics": {"heldout_test": {"precision_at_k": 0.0014}},
                     "session_model": {"index_file": "session_neighbors.sqlite", "configuration": {},
                                       "model_version": "session-v2", "metric_model_version": "session-eval-v2",
                                       "metrics": {"selected": {"precision_at_k": 0.02},
                                                   "current_item_item": {"precision_at_k": 0.01},
                                                   "popularity": {"precision_at_k": 0.005}}}}
        self.write_bundle()
        (self.outputs / "latest_run.json").write_text('{"run_id":"run-session"}', encoding="utf-8")
        self.graph = self.serving / "session_neighbors.sqlite"
        with sqlite3.connect(self.graph) as connection:
            connection.execute("CREATE TABLE neighbors(seed INTEGER,candidate INTEGER,score REAL,PRIMARY KEY(seed,candidate)) WITHOUT ROWID")
            connection.executemany("INSERT INTO neighbors VALUES(?,?,?)",
                                   [(10, 11, .9), (10, 12, .5), (20, 21, .9), (20, 22, .5), (1, 2, .8)])

    def tearDown(self):
        self.client.close()
        self.online.close()
        self.temporary.cleanup()

    def write_bundle(self):
        next_file = self.serving / "next.json"
        next_file.write_text(json.dumps(self.data), encoding="utf-8")
        os.replace(next_file, self.serving / "model_bundle.json")

    def request(self, item=10, **changes):
        body = {"events": [{"itemid": item, "event": "view", "timestamp": int(time.time() * 1000)}], "k": 2}
        body.update(changes)
        return self.client.post("/api/session/recommendations", json=body)

    def test_http_roundtrip_uses_real_graph_and_distinct_versions(self):
        result = self.request()
        self.assertEqual(result.status_code, 200, result.text)
        data = result.json()
        self.assertEqual(data["model_version"], "static-v2")
        self.assertEqual(data["session_model_version"], "session-v2")
        self.assertEqual(data["metric_model_version"], "session-eval-v2")
        self.assertEqual(data["source"], "session_item_item")
        self.assertEqual([row["itemid"] for row in data["items"]], [11, 12])
        self.assertEqual(data["context_items"], 1)
        self.assertEqual(data["matched_seeds"], 1)
        self.assertFalse(data["cache_hit"])
        self.assertEqual(data["persistence"], "client_context_only")

    def test_contexts_are_independent_and_empty_context_is_explicit_fallback(self):
        first = self.request(10).json()
        other = self.request(20).json()
        again = self.request(10).json()
        self.assertEqual(first["items"], again["items"])
        self.assertEqual([row["itemid"] for row in other["items"]], [21, 22])
        empty = self.client.post("/api/session/recommendations", json={"events": [], "k": 2}).json()
        self.assertEqual(empty["source"], "popular_fallback")
        self.assertEqual(empty["context_events"], 0)
        self.assertEqual([row["itemid"] for row in empty["items"]], [1, 2])

    def test_context_and_known_user_history_are_both_excluded(self):
        data = self.request(visitorid=7).json()
        self.assertEqual(data["source"], "session_item_item")
        self.assertNotIn(11, [row["itemid"] for row in data["items"]])
        self.assertNotIn(10, [row["itemid"] for row in data["items"]])
        self.assertNotIn(1, [row["itemid"] for row in data["items"]])
        self.assertEqual(data["seen_items_excluded"], 3)
        self.assertIn("bundle", data["history_sources"])
        unknown = self.request(visitorid=999).json()
        self.assertEqual(unknown["items"][0]["itemid"], 11)

    def test_session_reads_canonical_online_history_without_cross_run_leakage(self):
        metrics = self.serving.parent / "metrics"
        metrics.mkdir()
        (metrics / "run_report.json").write_text(json.dumps({"run_id": "run-session", "source_run_id": "run-ingest"}), encoding="utf-8")
        calls = []
        def history(run, visitor):
            calls.append((run, visitor))
            return ({12} if run == "run-ingest" else set()), ["fixture_history"], []
        self.online.history = history
        result = self.request(visitorid=7).json()
        self.assertEqual(result["history_runs"], ["run-session", "run-ingest"])
        self.assertEqual(calls, [("run-session", 7), ("run-ingest", 7)])
        self.assertNotIn(12, [row["itemid"] for row in result["items"]])
        self.assertNotIn(11, [row["itemid"] for row in result["items"]])
        calls.clear()
        self.request()
        self.assertEqual(calls, [])

    def test_session_filters_full_sqlite_history_for_user_outside_exported_cache(self):
        self.data["seen_items_store"] = "seen_items.sqlite"
        with sqlite3.connect(self.serving / "seen_items.sqlite") as connection:
            connection.execute("CREATE TABLE seen_items(visitorid INTEGER,itemid INTEGER,PRIMARY KEY(visitorid,itemid)) WITHOUT ROWID")
            connection.execute("INSERT INTO seen_items VALUES(888,11)")
        self.write_bundle()
        result = self.request(visitorid=888).json()
        self.assertNotIn(11, [row["itemid"] for row in result["items"]])
        self.assertIn("sqlite", result["history_sources"])
        self.assertEqual(result["items"][0]["itemid"], 12)

    def test_omitted_timestamp_uses_server_clock_and_event_types_are_supported(self):
        for event in ("view", "addtocart", "transaction"):
            with self.subTest(event=event):
                result = self.client.post("/api/session/recommendations", json={"events": [{"itemid": 10, "event": event}], "k": 2})
                self.assertEqual(result.status_code, 200, result.text)
                self.assertEqual(result.json()["context_events"], 1)

    def test_rejects_future_invalid_and_unbounded_payloads(self):
        invalid = [
            {"visitorid": -1}, {"visitorid": True}, {"visitorid": "7"}, {"visitorid": 2147483648},
            {"k": 0}, {"k": 101}, {"k": 1.0}, {"events": [{"itemid": True}]},
            {"events": [{"itemid": 10.0}]}, {"events": [{"itemid": -1}]},
            {"events": [{"itemid": 10, "event": "fake"}]},
            {"events": [{"itemid": 10, "timestamp": -1}]},
            {"events": [{"itemid": 10, "timestamp": int(time.time() * 1000) + 3600000}]},
            {"events": [{"itemid": 10, "extra": "unexpected"}]},
            {"events": [{"itemid": 10}] * 101}, {"model_version": "fake"},
        ]
        for body in invalid:
            with self.subTest(body=body):
                self.assertEqual(self.client.post("/api/session/recommendations", json=body).status_code, 422)

    def test_missing_index_refuses_session_but_keeps_static_model_available(self):
        self.graph.unlink()
        self.assertEqual(self.request().status_code, 503)
        self.assertEqual(self.client.get("/api/recommendations/7").status_code, 200)
        metrics = self.client.get("/api/metrics").json()
        self.assertFalse(metrics["session_model"]["available"])

    def test_malformed_schema_and_traversal_refused(self):
        self.graph.unlink()
        with sqlite3.connect(self.graph) as connection:
            connection.execute("CREATE TABLE wrong_table(item INTEGER)")
        self.assertEqual(self.request().status_code, 503)
        self.data["session_model"]["index_file"] = "../../outside.sqlite"
        self.write_bundle()
        fresh = TestClient(create_app(self.settings))
        try:
            self.assertEqual(fresh.post("/api/session/recommendations", json={}).status_code, 503)
        finally:
            fresh.close()

    def test_metrics_are_separate_and_do_not_overwrite_static_evaluation(self):
        data = self.client.get("/api/metrics").json()
        self.assertEqual(data["model_metrics"]["heldout_test"]["precision_at_k"], .0014)
        self.assertTrue(data["session_model"]["available"])
        self.assertEqual(data["session_model"]["metrics"]["selected"]["precision_at_k"], .02)
        page = self.client.get("/")
        self.assertIn('id="session-section"', page.text)
        self.assertIn('id="session-evaluation"', page.text)

    def test_old_bundle_without_session_is_still_servable(self):
        del self.data["session_model"]
        self.write_bundle()
        self.assertEqual(self.client.get("/api/recommendations/7").status_code, 200)
        self.assertEqual(self.request().status_code, 503)
        self.assertIsNone(self.client.get("/api/metrics").json()["session_model"])


if __name__ == "__main__":
    unittest.main()
