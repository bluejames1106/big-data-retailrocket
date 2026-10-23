import math
import os
import unittest

from pipeline.neighborhood_upgrade import rank_candidates,score_predictions,paired_bootstrap,selection_order


class NeighborhoodContractTests(unittest.TestCase):
    def test_no_personal_history_falls_back_with_zero_pop_weight(self):
        config={'weights':{'sar':1.,'als':0.,'popularity':0.}}
        rows=rank_candidates([],[],[10,20,30],{10},config,2)
        self.assertEqual([r['itemid'] for r in rows],[20,30])

    def test_seen_removed_duplicates_and_exact_k_fill(self):
        config={'weights':{'sar':.5,'als':.5,'popularity':0.}}
        rows=rank_candidates([10,20,20],[20,30],[10,20,30,40,50],{10},config,4)
        self.assertEqual([r['itemid'] for r in rows],[20,30,40,50])
        self.assertTrue(all(math.isfinite(r['score']) for r in rows))

    def test_metrics_preserve_cold_and_macro_denominator(self):
        cohort={'actual':{1:{20},2:{30}},'seen':{1:{10},2:set()},
                'base':{'training_catalog_size':3,'eligible_users':2,'cold_users':1}}
        baseline={1:[30],2:[30]};new={1:[20],2:[30]}
        metrics=score_predictions(cohort,new)
        self.assertEqual(metrics['precision_at_k'],.1)
        self.assertEqual(metrics['recall_at_k'],1.)
        self.assertEqual(metrics['total_hits'],2)
        self.assertEqual(metrics['cold_users'],1)
        interval=paired_bootstrap(cohort,new,baseline,100)
        self.assertAlmostEqual(interval['precision_at_k']['mean_difference'],.05)
        self.assertEqual(interval,paired_bootstrap(cohort,new,baseline,100))

    def test_balanced_validation_selection_does_not_reward_one_metric_only(self):
        baseline={'minimum_validation_ratio':1.,'mean_ndcg_at_k':.01,'mean_recall_at_k':.02,'mean_precision_at_k':.03}
        overfit={'minimum_validation_ratio':.9,'mean_ndcg_at_k':.5,'mean_recall_at_k':.01,'mean_precision_at_k':.2}
        self.assertEqual(max([baseline,overfit],key=selection_order),baseline)


@unittest.skipUnless(os.getenv('BIGDATA_RUN_SPARK_ML_TESTS')=='1','Spark tests opt in')
class NeighborhoodSparkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from pyspark.sql import SparkSession
        cls.spark=(SparkSession.builder.master('local[2]').appName('Neighborhood-Temporal-Tests')
                   .config('spark.sql.shuffle.partitions','2').getOrCreate())
        cls.spark.sparkContext.setLogLevel('ERROR')

    @classmethod
    def tearDownClass(cls):cls.spark.stop()

    def test_other_users_cooccurrence_learned_without_future_or_self_edges(self):
        from pipeline.neighborhood_upgrade import fit_neighborhoods
        rows=[(1,10,1),(1,20,2),(2,10,3),(2,20,4),(3,10,5),(3,30,6),(7,10,7),
              (7,99,100),(1,99,100),(2,99,100),(3,99,100)]
        history=self.spark.createDataFrame(rows,'visitorid int,itemid int,timestamp long')
        users=self.spark.createDataFrame([(7,),(999,)],'visitorid int')
        graphs=[{'similarity':'cosine','min_cooccurrence':1},{'similarity':'jaccard','min_cooccurrence':2}]
        values=fit_neighborhoods(history,10,users,graphs)
        self.assertEqual(values['cosine-min1'][7],[20,30])
        self.assertEqual(values['jaccard-min2'][7],[20])
        self.assertNotIn(999,values['cosine-min1'])
        self.assertNotIn(99,values['cosine-min1'][7])

    def test_secondary_is_disjoint_and_keeps_novelty_rule(self):
        from pipeline.hybrid_upgrade import make_cohort
        from pipeline.neighborhood_upgrade import secondary_cohort
        history=self.spark.createDataFrame([(1,10),(2,20)],'visitorid int,itemid int')
        future=self.spark.createDataFrame([(1,10),(1,11),(2,21),(3,30),(4,40)],'visitorid int,itemid int')
        primary=make_cohort(history,future,cap=1)
        secondary=secondary_cohort(history,future,primary,cap=2)
        try:
            self.assertEqual(len(secondary['actual']),2)
            self.assertFalse(set(primary['actual'])&set(secondary['actual']))
            for u,actual in secondary['actual'].items():self.assertFalse(actual&secondary['seen'][u])
        finally:primary['users'].unpersist();secondary['users'].unpersist()


if __name__=='__main__':unittest.main()
