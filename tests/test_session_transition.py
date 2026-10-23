from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest

from pipeline.session_model import score_context
from pipeline.session_transition import fit_transitions, fit_continuations, fused_rank, strength_fused_rank


class TransitionTests(unittest.TestCase):
    def test_training_uses_only_prior_timestamp_groups_and_new_items(self):
        data = SimpleNamespace(a={'timestamp': [10, 10, 20, 30, 10, 20],
                                  'itemid': [1, 2, 3, 1, 1, 4]},
                               starts=[0, 4], ends=[4, 6])
        graph, profile = fit_transitions(data, 20)
        self.assertEqual(graph[1], [(3, 1), (4, 1)])
        self.assertEqual(graph[2], [(3, 1)])
        self.assertEqual(profile['observed_transitions'], 3)
        self.assertNotIn(2, dict(graph[1]))  # Same-timestamp item never becomes a target.

    def test_sqlite_inference_matches_offline_fusion_and_filters_seen(self):
        co = {10: [(20, .8), (30, .7)]}
        transitions = {10: [(30, 5), (40, 2)]}
        events = [{'itemid': 10, 'event': 'view', 'timestamp': 100}]
        config = {'query_half_life_minutes': None, 'event_weights': {'view': 1},
                  'position_decay': .8, 'seed_limit': 5, 'transition_weight': .25}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'session_neighbors.sqlite'
            with closing(sqlite3.connect(path)) as db:
                db.execute('CREATE TABLE neighbors(seed INTEGER,candidate INTEGER,score REAL,PRIMARY KEY(seed,candidate)) WITHOUT ROWID')
                db.execute('CREATE TABLE transitions(seed INTEGER,candidate INTEGER,score REAL,PRIMARY KEY(seed,candidate)) WITHOUT ROWID')
                db.executemany('INSERT INTO neighbors VALUES (?,?,?)', [(10, 20, .8), (10, 30, .7)])
                db.executemany('INSERT INTO transitions VALUES (?,?,?)', [(10, 30, 5), (10, 40, 2)])
                db.commit()
            online = score_context(path, events, [50, 60], {20}, 3, config, 100)
        offline = fused_rank(co, transitions, events, [50, 60], {20}, .25, 3, config, 100)
        self.assertEqual(online, offline)
        self.assertEqual([r['itemid'] for r in online['items']], [30, 40, 50])
        self.assertEqual(online['source'], 'session_item_item_transition')

    def test_continuation_graph_uses_only_strictly_later_new_items(self):
        data = SimpleNamespace(a={'timestamp': [10, 10, 20, 30, 40],
                                  'itemid': [1, 2, 3, 1, 4]}, starts=[0], ends=[5])
        graph, profile = fit_continuations(data, 30)
        self.assertEqual(graph[1], [(3, 1)])
        self.assertEqual(graph[2], [(3, 1)])
        self.assertEqual(profile['observed_continuations'], 2)

    def test_strength_sqlite_inference_matches_offline_and_filters_seen(self):
        co = {10: [(20, .8), (30, .7)]}
        transitions = {10: [(30, 5), (40, 2)]}
        events = [{'itemid': 10, 'event': 'view', 'timestamp': 100}]
        config = {'query_half_life_minutes': None, 'event_weights': {'view': 1},
                  'position_decay': .8, 'seed_limit': 5, 'transition_weight': .25,
                  'fusion': 'strength', 'strength_power': .5}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'session_neighbors.sqlite'
            with closing(sqlite3.connect(path)) as db:
                db.execute('CREATE TABLE neighbors(seed INTEGER,candidate INTEGER,score REAL,PRIMARY KEY(seed,candidate)) WITHOUT ROWID')
                db.execute('CREATE TABLE transitions(seed INTEGER,candidate INTEGER,score REAL,PRIMARY KEY(seed,candidate)) WITHOUT ROWID')
                db.executemany('INSERT INTO neighbors VALUES (?,?,?)', [(10, 20, .8), (10, 30, .7)])
                db.executemany('INSERT INTO transitions VALUES (?,?,?)', [(10, 30, 5), (10, 40, 2)])
                db.commit()
            online = score_context(path, events, [50, 60], {20}, 3, config, 100)
        offline = strength_fused_rank(co, transitions, events, [50, 60], {20}, .25, .5, 3, config, 100)
        self.assertEqual(online, offline)
        self.assertEqual([r['itemid'] for r in online['items']], [30, 40, 50])


if __name__ == '__main__':
    unittest.main()
