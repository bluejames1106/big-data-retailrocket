"""Small sparse-matrix and temporal-cohort fixtures; no Spark or full-data scan."""
import hashlib
import unittest

import numpy as np

from pipeline.session_upgrade import EventData, SparseGraph


def data(rows):
    """Rows are visitor, item, millisecond timestamp, optional event code."""
    return EventData({"visitorid": np.array([r[0] for r in rows], dtype=np.int32),
                      "itemid": np.array([r[1] for r in rows], dtype=np.int32),
                      "timestamp": np.array([r[2] for r in rows], dtype=np.int64),
                      "event": np.array([r[3] if len(r) > 3 else 0 for r in rows], dtype=np.int8)})


class SparseTrainingTests(unittest.TestCase):
    def test_binary_graph_matches_exact_jaccard_shrinkage_counts(self):
        fixture = data([(1, 10, 1), (1, 20, 2), (2, 10, 3), (2, 20, 4),
                        (3, 10, 5), (3, 30, 6), (7, 10, 7)])
        graph = SparseGraph(fixture, 10, "binary_user")
        result = graph.neighbors([10])[10]
        self.assertEqual([item for item, _ in result], [20, 30])
        self.assertAlmostEqual(result[0][1], (2 / (4 + 2 - 2)) * (2 / (2 + 3)))
        self.assertAlmostEqual(result[1][1], (1 / (4 + 1 - 1)) * (1 / (1 + 3)))
        self.assertEqual(graph.frequency[10], 4)

    def test_binary_graph_caps_latest_twenty_distinct_pairs_per_visitor(self):
        fixture = data([(1, item, item) for item in range(1, 23)] + [(2, 1, 1), (2, 100, 2)])
        graph = SparseGraph(fixture, 30, "binary_user")
        self.assertEqual(graph.profile["entity_item_pairs_before_cap"], 24)
        self.assertEqual(graph.profile["entity_item_pairs_after_cap"], 22)
        self.assertEqual(graph.frequency[1], 1)
        self.assertEqual(graph.neighbors([1])[1], [(100, .25)])
        self.assertEqual([item for item, _ in graph.neighbors([22])[22]], list(range(3, 22)))

    def test_cap_breaks_equal_timestamp_ties_by_ascending_item_id(self):
        fixture = data([(1, item, 100) for item in range(21, 0, -1)])
        graph = SparseGraph(fixture, 100, "binary_user")
        self.assertEqual(set(graph.catalog), set(range(1, 21)))
        self.assertEqual(graph.neighbors([21])[21], [])

    def test_weighted_graph_uses_maximum_decayed_intent_not_event_sum(self):
        rows = [(1, 10, 10, 2), (1, 10, 20, 0), (1, 20, 15, 0),
                (2, 10, 1, 0), (2, 30, 2, 0)]
        original = SparseGraph(data(rows), 100, "weighted_user")
        repeated = SparseGraph(data(rows + [(1, 10, 20, 0)] * 100), 100, "weighted_user")
        expected = 5. * 2. ** (-(100 - 10) / (30 * 86400000))
        self.assertAlmostEqual(original.matrix[1, 10], expected)
        self.assertEqual(original.profile["entity_item_pairs_after_cap"], 4)
        self.assertEqual(original.matrix[1, 10], repeated.matrix[1, 10])
        self.assertEqual(original.neighbors([10]), repeated.neighbors([10]))
        self.assertEqual(repeated.frequency[10], 2)

    def test_future_events_do_not_change_any_graph_or_catalog(self):
        past = [(1, 10, 1), (1, 20, 2), (2, 10, 3), (2, 20, 4), (3, 30, 5)]
        future = [(1, 99, 101, 2), (2, 99, 102, 2), (3, 10, 103, 2)]
        for kind in ("binary_user", "weighted_user", "weighted_session"):
            with self.subTest(kind=kind):
                old = SparseGraph(data(past), 100, kind)
                new = SparseGraph(data(past + future), 100, kind)
                self.assertEqual(old.neighbors([10, 20, 30]), new.neighbors([10, 20, 30]))
                self.assertEqual(set(old.catalog), set(new.catalog))
                self.assertEqual(new.neighbors([99])[99], [])
                self.assertEqual(new.profile["input_events"], len(past))

    def test_session_graph_does_not_link_items_across_separate_sessions(self):
        fixture = data([(1, 10, 0), (1, 20, 60000), (1, 30, 2000000), (1, 40, 2000001)])
        visitor_graph = SparseGraph(fixture, 3000000, "weighted_user")
        session_graph = SparseGraph(fixture, 3000000, "weighted_session")
        self.assertIn(30, [item for item, _ in visitor_graph.neighbors([10])[10]])
        self.assertEqual([item for item, _ in session_graph.neighbors([10])[10]], [20])
        self.assertEqual(session_graph.profile["entities"], 2)


class SessionCohortTests(unittest.TestCase):
    def test_simultaneous_events_are_never_each_others_past_context(self):
        fixture = data([(1, 20, 100), (1, 10, 100), (1, 40, 101), (1, 30, 101)])
        rows, population = fixture.session_rows(0, 200)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["actual"], [30])
        self.assertEqual(row["seen"], [10, 20])
        self.assertEqual([event["itemid"] for event in row["context"]], [10, 20])
        self.assertTrue(all(event["timestamp"] <= row["as_of"] < row["target_timestamp"] for event in row["context"]))
        self.assertEqual(row["as_of"], 100)
        self.assertEqual(population["first_timestamp_groups_without_context"], 1)

    def test_first_new_target_skips_repeats_and_excludes_later_events(self):
        fixture = data([(1, 10, 1), (1, 10, 2, 1), (1, 20, 3), (1, 30, 4)])
        rows, population = fixture.session_rows(0, 10)
        row = rows[0]
        self.assertEqual(row["actual"], [20])
        self.assertEqual(row["target_timestamp"], 3)
        self.assertEqual(row["seen"], [10])
        self.assertEqual([event["itemid"] for event in row["context"]], [10, 10])
        self.assertEqual(population["repeated_events_before_first_query"], 1)

    def test_thirty_minute_gap_is_inclusive_and_larger_gap_starts_new_context(self):
        fixture = data([(1, 10, 0), (1, 20, 1800000), (1, 30, 3600001), (1, 40, 3600002)])
        self.assertEqual(len(fixture.starts), 2)
        rows, population = fixture.session_rows(0, 4000000)
        by_target = {row["actual"][0]: row for row in rows}
        self.assertEqual(set(by_target), {20, 40})
        self.assertEqual(by_target[20]["seen"], [10])
        self.assertEqual(by_target[40]["seen"], [30])
        self.assertEqual(population["active_sessions"], 2)

    def test_coverage_reports_sessions_without_eligible_context_and_target(self):
        fixture = data([(1, 10, 10), (2, 20, 10), (2, 20, 11), (3, 30, 10), (3, 31, 11)])
        rows, population = fixture.session_rows(0, 20)
        self.assertEqual(population["active_sessions"], 3)
        self.assertEqual(population["eligible_sessions"], 1)
        self.assertEqual(population["ineligible_sessions"], 2)
        self.assertEqual(population["sampled_queries"], 1)
        self.assertEqual(population["first_timestamp_groups_without_context"], 3)
        self.assertEqual(population["repeated_events_before_first_query"], 1)
        self.assertEqual(rows[0]["visitorid"], 3)

    def test_cold_catalog_targets_are_retained(self):
        fixture = data([(2, 10, 0), (1, 10, 11), (1, 999, 12)])
        rows, _ = fixture.session_rows(10, 20)
        train_catalog = set(fixture.a["itemid"][fixture.a["timestamp"] <= 10])
        self.assertNotIn(999, train_catalog)
        self.assertEqual(rows[0]["actual"], [999])

    def test_sampling_uses_session_keys_and_is_independent_of_input_order_and_target_ids(self):
        records = [(user, 1000 + user, 100) for user in range(1, 31)]
        records += [(user, 2000 + user, 101) for user in range(1, 31)]
        rows, population = data(records).session_rows(0, 200, cap=7)
        expected = sorted((f"{user}:100" for user in range(1, 31)),
                          key=lambda key: (hashlib.sha256((key + ":42").encode()).hexdigest(), key))[:7]
        expected.sort()
        self.assertEqual([row["query_id"] for row in rows], expected)
        reversed_rows, _ = data(records[::-1]).session_rows(0, 200, cap=7)
        changed = [(u, item + 5000 if stamp == 101 else item, stamp) for u, item, stamp in records]
        changed_rows, _ = data(changed).session_rows(0, 200, cap=7)
        self.assertEqual([row["query_id"] for row in reversed_rows], expected)
        self.assertEqual([row["query_id"] for row in changed_rows], expected)
        self.assertEqual(population["eligible_sessions"], 30)
        self.assertEqual(population["sampled_queries"], 7)

    def test_period_end_and_global_cutoff_are_respected(self):
        fixture = data([(1, 10, 1), (1, 20, 3), (1, 30, 6), (2, 40, 10), (2, 50, 11)])
        rows, population = fixture.session_rows(3, 6)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["actual"], [30])
        self.assertEqual(rows[0]["seen"], [10, 20])
        self.assertEqual(population["active_sessions"], 1)


if __name__ == "__main__":
    unittest.main()
