"""Validation-selected ALS/popularity policy using the committed full-data lake.

Reuse the existing ALS factors; learn recent popularity from past events only.
Preserve the old cohort, K, novelty rule and cold users. Do not tune on test.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import gc
import json
import math
import os
from pathlib import Path
import shutil
import sqlite3
import time

from pipeline.config import atomic_json, pipeline_lock, run_path, utc_now
from pipeline.ranking import rolling_folds, user_ranking_metrics
from pipeline.train import prepare_interactions, fit_als_pipeline, recommend_unseen

WINDOWS = (0, 7, 30)
WEIGHTS = (0.0, 0.5, 0.8, 1.0)
K = 10
ALS_POOL = 100


def fuse_candidates(als_items, popular_items, seen, als_weight, k=10):
    """RRF over unseen ALS top-100 and a deterministic popularity ranking.

    Cold users always receive popularity. Scores are ranking values, not odds.
    Popularity input must cover at least max_seen+1000 items of its full ranking.
    """
    if not 0 <= als_weight <= 1 or k < 1:
        raise ValueError('Invalid fusion parameters')
    seen = set(seen)
    als = list(dict.fromkeys(int(i) for i in als_items if i not in seen))[:ALS_POOL]
    popular = list(dict.fromkeys(int(i) for i in popular_items if i not in seen))[:max(1000, k)]
    weight = als_weight if als else 0.0
    scores = {}
    for ranking, factor in ((als, weight), (popular, 1-weight)):
        if factor <= 0:
            continue
        for rank, item in enumerate(ranking, 1):
            scores[item] = scores.get(item, 0.0) + factor / (60+rank)
    return [{'itemid': item, 'score': score} for item, score in
            sorted(scores.items(), key=lambda row: (-row[1], row[0]))[:k]]


def make_cohort(history, holdout, cap=1000):
    from pyspark.sql import functions as F
    pairs = history.select('visitorid','itemid').distinct()
    all_targets = holdout.select('visitorid','itemid').distinct()
    novel = all_targets.join(pairs,['visitorid','itemid'],'left_anti').cache()
    population_pairs = novel.count()
    population_users = novel.select('visitorid').distinct().count()
    users = (novel.select('visitorid').distinct()
             .orderBy(F.xxhash64('visitorid',F.lit(42)),'visitorid').limit(cap).cache())
    actual = {}
    for row in novel.join(users,'visitorid').collect():
        actual.setdefault(int(row.visitorid),set()).add(int(row.itemid))
    seen = {user:set() for user in actual}
    for row in pairs.join(users,'visitorid').collect():
        seen[int(row.visitorid)].add(int(row.itemid))
    total = all_targets.join(users,'visitorid').count()
    catalog = pairs.select('itemid').distinct().cache()
    catalog_size = catalog.count()
    cold_items = novel.join(users,'visitorid').join(catalog,'itemid','left_anti').count()
    catalog.unpersist(); novel.unpersist()
    return {'actual':actual,'seen':seen,'users':users,
            'base':{'k':K,'eligible_users':len(actual),'population_eligible_users':population_users,
                    'population_novel_pairs':population_pairs,'heldout_unique_pairs':total,
                    'novel_heldout_pairs':sum(map(len,actual.values())),
                    'excluded_repeat_pairs':total-sum(map(len,actual.values())),
                    'training_catalog_size':catalog_size,'cold_users':sum(not v for v in seen.values()),
                    'cold_item_pairs':cold_items,
                    'evaluation_sampling':'lowest xxhash64(visitorid,42), capped at 1000; same source cohort as previous ALS',
                    'definition':'macro user ranking over novel held-out items; precision denominator K; cold users retained'}}


def popularity(history, cutoff, days, limit):
    from pyspark.sql import functions as F
    past = history.filter(F.col('timestamp') <= cutoff)
    if days:
        past = past.filter(F.col('timestamp') > cutoff-days*86400000)
    # Each visitor gets one vote per item; repeated views cannot dominate.
    rows = (past.select('visitorid','itemid').distinct().groupBy('itemid').count()
            .orderBy(F.desc('count'),F.asc('itemid')).limit(limit).collect())
    return [int(row.itemid) for row in rows]


def als_lists(model, history, users, catalog_size):
    predictions = recommend_unseen(model,users,history,ALS_POOL,catalog_size)
    values = {}
    for row in predictions.orderBy('visitorid','rank').collect():
        values.setdefault(int(row.visitorid),[]).append(int(row.itemid))
    predictions.unpersist()
    return values


def score_policy(cohort, als, popular, weight, raw_als=False):
    actual, seen = cohort['actual'],cohort['seen']
    rows, hits, predicted_items = [],0,set()
    warm = []
    predictions = {}
    for user, target in actual.items():
        items = (als.get(user,[])[:K] if raw_als else
                 [row['itemid'] for row in fuse_candidates(als.get(user,[]),popular,seen[user],weight,K)])
        assert not set(items)&seen[user] and len(items)==len(set(items))
        predictions[user] = items
        metrics = user_ranking_metrics(items,target,K)
        rows.append(metrics)
        if seen[user]:warm.append(metrics)
        hits += len(set(items)&target)
        predicted_items.update(items)
    def means(values):
        return {key:sum(row[key] for row in values)/len(values) if values else 0.0
                for key in ('precision_at_k','recall_at_k','ndcg_at_k')}
    result = {**cohort['base'],**means(rows),'status':'evaluated','total_hits':hits,
              'catalog_coverage':len(predicted_items)/cohort['base']['training_catalog_size'],
              'user_coverage':sum(bool(v) for v in predictions.values())/len(actual),
              'warm_users_only_diagnostic':{'users':len(warm),**means(warm)}}
    return result


def execute(source_run, run_id, output_root='outputs'):
    os.environ.setdefault('PYSPARK_SUBMIT_ARGS','--driver-memory 3g --conf spark.driver.maxResultSize=512m --conf spark.local.dir=/tmp/full_spark_scratch pyspark-shell')
    os.environ['BIGDATA_LOG_CONFIDENCE']='1'
    os.environ['BIGDATA_ALS_BLOCKS']='6'
    source = run_path(source_run,output_root)
    root = run_path(run_id,output_root)
    if root.exists():raise FileExistsError('Use a new run ID; previous evidence is immutable')
    source_report=json.loads((source/'metrics/run_report.json').read_text())
    assert source_report['status']=='completed'
    old_metrics=json.loads((source/'metrics/model_metrics.json').read_text())
    split=json.loads((source/'metrics/temporal_split.json').read_text())
    assert split['input_events']==2755641
    parameters=old_metrics['selected_parameters']
    report={'run_id':run_id,'source_run_id':source_run,'status':'running','started_at_utc':utc_now(),
            'clean_event_count':2755641,'scope':'Full-history offline model policy upgrade; existing ALS factors reused; no Kafka replay',
            'stages':{name:source_report['stages'][name] for name in ('history_import','lake','acid')}}
    report['stages']['acid']={**report['stages']['acid'],'reused_from_run_id':source_run}
    start=time.monotonic()
    spark=None
    with pipeline_lock(output_root):
        def stage(name):
            report['current_stage']=name
            atomic_json(root/'metrics/run_report.json',report)
            print(f'[{utc_now()}] UPGRADE: {name}',flush=True)
        try:
            from phase3.streaming import create_spark
            from pyspark.sql import functions as F
            from pyspark.ml import PipelineModel
            from delta.tables import DeltaTable
            stage('read_frozen_full_source')
            spark=create_spark('local[4]',enable_delta=True,download_jars=True)
            spark.conf.set('spark.sql.shuffle.partitions','24')
            spark.sparkContext.setCheckpointDir('/tmp/hybrid_checkpoints')
            version=DeltaTable.forPath(spark,split['source']).history(1).select('version').first().version
            assert int(version)==split['delta_version']
            events=(spark.read.format('delta').option('versionAsOf',version).load(split['source'])
                    .selectExpr('timestamp','cast(visitorid as int) visitorid',
                                'cast(itemid as int) itemid','event').cache())
            assert events.count()==2755641
            configurations=[{'popularity_window_days':days,'als_weight':weight,'rrf_constant':60}
                            for days in WINDOWS for weight in WEIGHTS]
            folds=[]
            for index,fold in enumerate(rolling_folds(split['boundaries']),1):
                stage(f'validation_fold_{index}')
                history=events.filter(F.col('timestamp')<=fold['train_end_inclusive']).cache()
                future=events.filter((F.col('timestamp')>fold['validation_start_exclusive']) &
                                     (F.col('timestamp')<=fold['validation_end_inclusive']))
                cohort=make_cohort(history,future)
                interactions=prepare_interactions(history).cache()
                fitted=fit_als_pipeline(interactions,parameters,max_iter=4)
                als=als_lists(fitted.stages[-1],history,cohort['users'],cohort['base']['training_catalog_size'])
                limit=max(2000,max(map(len,cohort['seen'].values()))+1000)
                popular={days:popularity(history,fold['train_end_inclusive'],days,limit) for days in WINDOWS}
                scores=[{'configuration':config,'metrics':score_policy(cohort,als,popular[config['popularity_window_days']],config['als_weight'])}
                        for config in configurations]
                evidence={'fold':fold,'results':scores,'als_without_fallback':score_policy(cohort,als,[],1,raw_als=True)}
                atomic_json(root/f'metrics/policy_validation_fold_{index}.json',evidence)
                folds.append(evidence)
                fitted.stages[-1].userFactors.unpersist(); fitted.stages[-1].itemFactors.unpersist()
                history.unpersist();interactions.unpersist();cohort['users'].unpersist()
                del fitted,cohort,als,popular
                gc.collect();spark._jvm.java.lang.System.gc()
                best=max(scores,key=lambda v:v['metrics']['ndcg_at_k'])
                print(f'VALIDATION {index} best: {json.dumps(best)}',flush=True)
            candidates=[]
            for index,config in enumerate(configurations):
                scores=[fold['results'][index]['metrics'] for fold in folds]
                candidates.append({'configuration':config,'folds':scores,
                                   **{f'mean_{key}':sum(s[key] for s in scores)/len(scores)
                                      for key in ('precision_at_k','recall_at_k','ndcg_at_k')}})
            order=lambda row:(row['mean_ndcg_at_k'],row['mean_recall_at_k'],row['mean_precision_at_k'])
            selected=max(candidates,key=order)
            baseline=max((row for row in candidates if row['configuration']['als_weight']==0),key=order)
            selection={'selected':selected,'popularity_baseline':baseline,'all_candidates':candidates,
                       'selected_at_utc':utc_now(),'source':split,'als_parameters':parameters,
                       'selection_metric':'mean validation NDCG, then recall, then precision; deterministic candidate order',
                       'selection_scope':'Only the two historical validation folds; written BEFORE test evaluation',
                       'test_reuse_disclosure':'Same previously reported test period and cohort for comparison; not a fresh unseen benchmark after iterative project development.'}
            atomic_json(root/'metrics/policy_selection.json',selection)
            stage('evaluate_frozen_policy_on_same_test_cohort')
            cutoff=split['boundaries'][-1]
            history=events.filter(F.col('timestamp')<=cutoff).cache()
            cohort=make_cohort(history,events.filter(F.col('timestamp')>cutoff))
            model=PipelineModel.load(str(source/'models/evaluation_pipeline'))
            als=als_lists(model.stages[-1],history,cohort['users'],cohort['base']['training_catalog_size'])
            limit=max(2000,max(map(len,cohort['seen'].values()))+1000)
            config=selected['configuration']
            popular=popularity(history,cutoff,config['popularity_window_days'],limit)
            heldout=score_policy(cohort,als,popular,config['als_weight'])
            baseline_pop=popularity(history,cutoff,baseline['configuration']['popularity_window_days'],limit)
            comparisons={'previous_reported_als':old_metrics['heldout_test'],
                         'als_recomputed_same_cohort':score_policy(cohort,als,[],1,raw_als=True),
                         'popularity_baseline':score_policy(cohort,als,baseline_pop,0),
                         'selected_policy':heldout}
            atomic_json(root/'metrics/test_comparison.json',comparisons)
            print('FROZEN TEST: '+json.dumps(comparisons),flush=True)
            model.stages[-1].userFactors.unpersist();model.stages[-1].itemFactors.unpersist()
            history.unpersist();cohort['users'].unpersist();del model,cohort,als
            gc.collect();spark._jvm.java.lang.System.gc()
            stage('refit_popularity_and_export_full_history_serving')
            old_bundle=json.loads((source/'serving/model_bundle.json').read_text())
            ids=sorted(int(v) for v in old_bundle['recommendations'])
            with closing(sqlite3.connect(source/'serving/seen_items.sqlite')) as db:
                seen={user:{int(row[0]) for row in db.execute('SELECT itemid FROM seen_items WHERE visitorid=?',(user,))} for user in ids}
            users=spark.createDataFrame([(u,) for u in ids],'visitorid int')
            serving_model=PipelineModel.load(str(source/'models/als_pipeline'))
            als=als_lists(serving_model.stages[-1],events,users,old_metrics['serving_scope']['training_catalog_size'])
            full_end=events.agg(F.max('timestamp')).first()[0]
            popular=popularity(events,full_end,config['popularity_window_days'],max(2000,max(map(len,seen.values()))+1000))
            exported={str(u):fuse_candidates(als.get(u,[]),popular,seen[u],config['als_weight'],100) for u in ids}
            serving=root/'serving';serving.mkdir(parents=True,exist_ok=True)
            shutil.copy2(source/'serving/seen_items.sqlite',serving/'seen_items.sqlite')
            algorithm='als_popularity_hybrid' if config['als_weight']>0 else 'popularity_recent'
            version=f'{run_id}:policy-serving-v1'
            metrics={**old_metrics,'run_id':run_id,'algorithm':algorithm,'source_run_id':source_run,
                     'heldout_test':heldout,'selected_policy':config,'policy_validation':selection,
                     'evaluated_at_utc':utc_now(),'trained_at_utc':utc_now(),
                     'metric_model_version':f'{run_id}:policy-evaluation-v1','serving_model_version':version,
                     'quality_comparison':comparisons,
                     'training_note':'Reuse full-history ALS factors; train unique-visitor popularity from all source events within selected recency window. Test factors trained before cutoff; serving factors use full history.',
                     'serving_scope':{**old_metrics['serving_scope'],'exported_recommendations_per_user':100,
                         'fallback':'validation-selected unique-visitor recent popularity; full SQLite seen filtering',
                         'all_recommendations_parquet':'models/recommendations','reused_als_factors_from':source_run}}
            atomic_json(root/'metrics/model_metrics.json',metrics)
            rows=[(int(u),row['itemid'],row['score'],rank) for u,items in exported.items() for rank,row in enumerate(items,1)]
            spark.createDataFrame(rows,'visitorid int,itemid int,score double,rank int').write.parquet(str(root/'models/recommendations'))
            atomic_json(serving/'model_bundle.json',{'run_id':run_id,'model_version':version,
                'trained_at_utc':metrics['trained_at_utc'],'algorithm':algorithm,
                'recommendations':exported,'seen_items':{str(u):sorted(seen[u]) for u in ids},
                'seen_items_store':'seen_items.sqlite',
                'popular_items':[{'itemid':item,'score':1/(60+rank)} for rank,item in enumerate(popular,1)],
                'metrics':metrics,'serving_scope':metrics['serving_scope']})
            kpis=json.loads((source/'metrics/kpis.json').read_text());kpis['run_id']=run_id
            atomic_json(root/'metrics/kpis.json',kpis)
            atomic_json(root/'metrics/temporal_split.json',split)
            report['stages']['training']=metrics
            stage('publish_registry_then_activate_atomically')
            from pipeline.runner import publish_model_registry
            from service.store import validate_bundle
            validate_bundle(json.loads((serving/'model_bundle.json').read_text()),run_id)
            report['stages']['model_registry']=publish_model_registry(root,run_id,os.environ['MONGO_URI'],os.environ['REDIS_URL'])
            report.update(status='completed',finished_at_utc=utc_now(),elapsed_seconds=round(time.monotonic()-start,3))
            atomic_json(root/'metrics/run_report.json',report)
            atomic_json(Path(output_root)/'latest_run.json',{'run_id':run_id,'model_version':version,'activated_at_utc':utc_now()})
            return report
        except Exception as error:
            report.update(status='failed',error=f'{type(error).__name__}: {error}',finished_at_utc=utc_now())
            atomic_json(root/'metrics/run_report.json',report)
            raise
        finally:
            if spark:spark.stop()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-run',required=True)
    parser.add_argument('--run-id',required=True)
    parser.add_argument('--output-root',default=os.getenv('OUTPUT_ROOT','outputs'))
    args=parser.parse_args()
    result=execute(args.source_run,args.run_id,args.output_root)
    print(json.dumps({'run_id':result['run_id'],'status':result['status']},indent=2))
