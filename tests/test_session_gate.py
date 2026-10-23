"""Avoid promoting numerically tied ranking metrics."""
import unittest
from pipeline.session_upgrade import strictly_better, METRICS

class SessionGateTests(unittest.TestCase):
    def test_equal_hit_totals_with_roundoff_are_not_improvement(self):
        base=dict.fromkeys(METRICS,.0014)
        candidate={k:v+1e-16 for k,v in base.items()}
        self.assertFalse(strictly_better(candidate,base))

    def test_every_metric_must_improve(self):
        base=dict.fromkeys(METRICS,.01)
        candidate=dict.fromkeys(METRICS,.02)
        self.assertTrue(strictly_better(candidate,base))
        candidate['ndcg_at_k']=.009
        self.assertFalse(strictly_better(candidate,base))
