"""Meaningful invariants shared by offline evaluation and HTTP session inference."""
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from pipeline.session_model import context_affinities, rank_context, score_context


def event(item, stamp, kind="view"):
    return {"itemid": item, "timestamp": stamp, "event": kind}


class SessionModelTests(unittest.TestCase):
    def setUp(self):
        self.config = {"query_half_life_minutes": None, "position_decay": 1., "seed_limit": 5,
                       "event_weights": {"view": 1., "addtocart": 3., "transaction": 5.},
                       "popularity_weight": 0.}

    def test_sqlite_http_helper_matches_in_memory_offline_ranker(self):
        neighbors = {10: [(50, .8), (30, .7), (20, .5)], 20: [(40, .9), (30, .4)]}
        events = [event(10, 900), event(20, 1000, "addtocart")]
        popular = [{"itemid": 30}, {"itemid": 60}, {"itemid": 70}]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session_neighbors.sqlite"
            with closing(sqlite3.connect(path)) as db:
                db.execute("CREATE TABLE neighbors (seed INTEGER, candidate INTEGER, score REAL, PRIMARY KEY(seed,candidate)) WITHOUT ROWID")
                db.executemany("INSERT INTO neighbors VALUES (?,?,?)",
                               [(seed, item, score) for seed, edges in neighbors.items() for item, score in edges])
                db.commit()
            online = score_context(path, events, popular, {50}, k=4, config=self.config, as_of=1000)
        offline = rank_context(neighbors, events, popular, {50}, k=4, config=self.config, as_of=1000)
        self.assertEqual(online, offline)
        self.assertEqual([value["itemid"] for value in online["items"]], [40, 30, 60, 70])
        self.assertEqual(online["matched_seeds"], 2)
        self.assertEqual(online["source"], "session_item_item")

    def test_cart_and_purchase_intent_change_candidate_order(self):
        graph = {10: [(100, 1.)], 20: [(200, .9)]}
        views = [event(10, 100), event(20, 90)]
        before = rank_context(graph, views, [], set(), 2, self.config, 100)
        self.assertEqual(before["items"][0]["itemid"], 100)
        for kind in ("addtocart", "transaction"):
            with self.subTest(event=kind):
                after = rank_context(graph, [event(10, 100), event(20, 90, kind)], [], set(), 2,
                                     self.config, 100)
                self.assertEqual(after["items"][0]["itemid"], 200)

    def test_time_decay_changes_rank_with_no_position_or_intent_change(self):
        graph = {10: [(100, 1.5)], 20: [(200, 1.)]}
        events = [event(10, 0), event(20, 60000)]
        before = rank_context(graph, events, [], set(), 2, self.config, 60000)
        decayed = dict(self.config, query_half_life_minutes=1.)
        after = rank_context(graph, events, [], set(), 2, decayed, 60000)
        self.assertEqual(before["items"][0]["itemid"], 100)
        self.assertEqual(after["items"][0]["itemid"], 200)
        affinities = dict(context_affinities(events, decayed, 60000))
        self.assertEqual(affinities[10], .5)
        self.assertEqual(affinities[20], 1.)

    def test_future_events_are_rejected_in_both_inference_entry_points(self):
        events = [event(10, 101)]
        with self.assertRaisesRegex(ValueError, "Future"):
            rank_context({10: [(20, 1.)]}, events, [30], set(), 1, self.config, 100)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "neighbors.sqlite"
            path.touch()
            with self.assertRaisesRegex(ValueError, "Future"):
                score_context(path, events, [30], set(), 1, self.config, 100)

    def test_unmatched_context_falls_back_without_seen_or_duplicate_items(self):
        result = rank_context({10: [(20, 1.)]}, [event(999, 100)], [999, 40, 40, 50, 60],
                              {50}, 2, self.config, 100)
        self.assertEqual(result["source"], "popular_fallback")
        self.assertEqual(result["matched_seeds"], 0)
        self.assertEqual([value["itemid"] for value in result["items"]], [40, 60])

    def test_seen_filter_covers_context_and_explicit_history_and_fallback(self):
        result = rank_context({10: [(10, 10.), (20, 9.), (30, .8)]}, [event(10, 100)],
                              [10, 20, 30, 30, 40], {20}, 5, self.config, 100)
        self.assertEqual([value["itemid"] for value in result["items"]], [30, 40])
        self.assertEqual(result["source"], "session_item_item")

    def test_duplicate_events_do_not_accumulate_unbounded_strength(self):
        graph = {10: [(100, 1.)], 20: [(200, 1.)]}
        events = [event(10, 100), event(20, 90, "addtocart")]
        repeated = events + [event(10, 100)] * 1000
        original = rank_context(graph, events, [], set(), 2, self.config, 100)
        duplicate = rank_context(graph, repeated, [], set(), 2, self.config, 100)
        self.assertEqual(original, duplicate)
        self.assertEqual(dict(context_affinities(repeated, self.config, 100))[10], 1.)
        self.assertEqual(original["items"][0]["itemid"], 200)

    def test_same_item_uses_max_event_strength_and_latest_timestamp(self):
        events = [event(10, 90, "transaction"), event(10, 100), event(20, 95, "addtocart")]
        config = dict(self.config, position_decay=.5)
        affinities = context_affinities(events, config, 100)
        self.assertEqual(affinities, [(10, 5.), (20, 1.5)])

    def test_seed_limit_uses_latest_distinct_items_with_deterministic_ties(self):
        events = [event(10, 0, "transaction"), event(30, 100), event(20, 100), event(30, 100)]
        config = dict(self.config, seed_limit=2)
        self.assertEqual(context_affinities(events, config, 100), [(20, 1.), (30, 1.)])


if __name__ == "__main__":
    unittest.main()
