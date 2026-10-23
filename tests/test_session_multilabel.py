from types import SimpleNamespace
import unittest

from pipeline.session_multilabel import remaining_session_labels
from pipeline.ranking import user_ranking_metrics


class MultiLabelSessionTests(unittest.TestCase):
    def test_future_labels_include_same_time_targets_and_later_new_items(self):
        data = SimpleNamespace(a={'timestamp': [10, 20, 20, 30, 40],
                                  'visitorid': [7, 7, 7, 7, 7],
                                  'itemid': [1, 2, 3, 2, 4]}, starts=[0], ends=[5])
        rows = [{'query_id': '7:10', 'seen': [1], 'as_of': 19, 'actual': [2]}]
        profile = remaining_session_labels(data, rows, 30)
        self.assertEqual(rows[0]['actual'], [2, 3])
        self.assertEqual(profile['queries_with_multiple_labels'], 1)
        self.assertEqual(user_ranking_metrics([3, 2] + list(range(10, 18)), rows[0]['actual'], 10),
                         {'precision_at_k': .2, 'recall_at_k': 1., 'ndcg_at_k': 1.})

    def test_existing_next_item_must_remain_in_future_labels(self):
        data = SimpleNamespace(a={'timestamp': [10, 20], 'visitorid': [7, 7],
                                  'itemid': [1, 2]}, starts=[0], ends=[2])
        with self.assertRaises(ValueError):
            remaining_session_labels(data, [{'query_id': '7:10', 'seen': [1],
                                             'as_of': 19, 'actual': [99]}], 20)


if __name__ == '__main__':
    unittest.main()
