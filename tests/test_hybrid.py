import math
import os
import unittest

from pipeline.hybrid_upgrade import fuse_candidates, score_policy


class FusionTests(unittest.TestCase):
    def test_cold_user_gets_popularity_even_when_als_weight_is_one(self):
        result=fuse_candidates([], [10,20,30], {10}, 1.0, 2)
        self.assertEqual([r['itemid'] for r in result],[20,30])

    def test_zero_weight_reproduces_unseen_popularity_without_duplicates(self):
        result=fuse_candidates([99,20],[10,20,20,30,40],{10},0,3)
        self.assertEqual([r['itemid'] for r in result],[20,30,40])

    def test_rrf_rewards_agreement_and_never_reintroduces_seen(self):
        result=fuse_candidates([99,20,30],[10,20,30,99],{10},0.5,3)
        self.assertEqual(result[0]['itemid'],20)
        self.assertEqual(len({r['itemid'] for r in result}),3)
        self.assertTrue(all(math.isfinite(r['score']) and r['score']>0 for r in result))
        self.assertEqual(result,fuse_candidates([99,20,30],[10,20,30,99],{10},0.5,3))

    def test_metrics_retain_cold_users_and_precision_denominator(self):
        cohort={'actual':{1:{10},2:{20}},'seen':{1:{9},2:set()},
                'base':{'training_catalog_size':10,'eligible_users':2,'cold_users':1}}
        raw=score_policy(cohort,{1:[10]},[],1,raw_als=True)
        hybrid=score_policy(cohort,{1:[10]},[20,10],1)
        self.assertAlmostEqual(raw['precision_at_k'],0.05)
        self.assertAlmostEqual(hybrid['precision_at_k'],0.1)
        self.assertEqual(raw['recall_at_k'],0.5)
        self.assertEqual(hybrid['recall_at_k'],1.0)
        self.assertEqual(hybrid['cold_users'],1)
        self.assertEqual(hybrid['eligible_users'],2)

    def test_invalid_weight_is_rejected(self):
        with self.assertRaises(ValueError):fuse_candidates([],[],set(),1.1)


@unittest.skipUnless(os.getenv('BIGDATA_RUN_SPARK_ML_TESTS')=='1','Spark tests opt in')
class TemporalPopularityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from pyspark.sql import SparkSession
        cls.spark=(SparkSession.builder.master('local[2]').appName('Hybrid-Temporal-Test')
                   .config('spark.sql.shuffle.partitions','2').getOrCreate())
        cls.spark.sparkContext.setLogLevel('ERROR')

    @classmethod
    def tearDownClass(cls):cls.spark.stop()

    def test_popularity_excludes_future_and_counts_visitors_once(self):
        from pipeline.hybrid_upgrade import popularity
        rows=[(1,10,1),(1,10,2),(1,10,3),(2,20,4),(3,20,5),
              (4,99,100),(5,99,100),(6,99,100)]
        events=self.spark.createDataFrame(rows,'visitorid int,itemid int,timestamp long')
        self.assertEqual(popularity(events,10,0,10),[20,10])

    def test_cohort_excludes_repeats_and_keeps_new_users(self):
        from pipeline.hybrid_upgrade import make_cohort
        history=self.spark.createDataFrame([(1,10)],'visitorid int,itemid int')
        future=self.spark.createDataFrame([(1,10),(1,20),(999,30)],'visitorid int,itemid int')
        cohort=make_cohort(history,future)
        try:
            self.assertEqual(cohort['actual'],{1:{20},999:{30}})
            self.assertEqual(cohort['base']['excluded_repeat_pairs'],1)
            self.assertEqual(cohort['base']['cold_users'],1)
            self.assertEqual(cohort['base']['eligible_users'],2)
        finally:cohort['users'].unpersist()


if __name__=='__main__':unittest.main()
