"""Pre-registered item-item study. Never tune using primary/secondary test.

Source: the existing full-history Delta snapshot; K/cold users/novel targets stay.
Only validation selects a model. A failed frozen test does not promote a model.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import gc
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sqlite3
import time

from pipeline.config import atomic_json, pipeline_lock, run_path, utc_now
from pipeline.hybrid_upgrade import make_cohort, popularity, als_lists
from pipeline.ranking import rolling_folds, user_ranking_metrics
from pipeline.train import prepare_interactions, fit_als_pipeline

METRICS=('precision_at_k','recall_at_k','ndcg_at_k')


def rank_candidates(als, neighbors, popular, seen, configuration, k=10):
    """Deterministic RRF. Missing personalized signals fall back to popularity."""
    seen=set(seen)
    rankings={'als':list(dict.fromkeys(int(i) for i in als if i not in seen))[:100],
              'sar':list(dict.fromkeys(int(i) for i in neighbors if i not in seen))[:100],
              'popularity':list(dict.fromkeys(int(i) for i in popular if i not in seen))[:max(1000,k)]}
    weights=configuration['weights']
    if any(weights[n]<0 for n in rankings) or abs(sum(weights.values())-1)>1e-8 or k<1:
        raise ValueError('Invalid rank weights or K')
    scores={}
    for name,ranking in rankings.items():
        for rank,item in enumerate(ranking,1):
            if weights[name]>0:scores[item]=scores.get(item,0)+weights[name]/(60+rank)
    if not scores:
        return [{'itemid':item,'score':1/(60+rank)} for rank,item in enumerate(rankings['popularity'][:k],1)]
    result=[{'itemid':item,'score':score} for item,score in sorted(scores.items(),key=lambda v:(-v[1],v[0]))[:k]]
    emitted={r['itemid'] for r in result}
    for item in rankings['popularity']:
        if len(result)>=k:break
        if item not in emitted:result.append({'itemid':item,'score':0.0});emitted.add(item)
    return result


def user_values(cohort, predictions):
    return {user:user_ranking_metrics(predictions.get(user,[]),target,10) for user,target in cohort['actual'].items()}


def score_predictions(cohort,predictions):
    values=user_values(cohort,predictions)
    for user,items in predictions.items():
        assert len(items)==len(set(items)) and not set(items)&cohort['seen'][user]
    hits=sum(len(set(predictions.get(u,[]))&target) for u,target in cohort['actual'].items())
    warm=[values[u] for u in values if cohort['seen'][u]]
    def mean(rows):return {key:sum(r[key] for r in rows)/len(rows) if rows else 0.0 for key in METRICS}
    items={i for ranking in predictions.values() for i in ranking}
    return {**cohort['base'],**mean(list(values.values())),'status':'evaluated','total_hits':hits,
            'hit_users':sum(bool(set(predictions.get(u,[]))&target) for u,target in cohort['actual'].items()),
            'user_coverage':sum(bool(predictions.get(u)) for u in values)/len(values),
            'catalog_coverage':len(items)/cohort['base']['training_catalog_size'],
            'warm_users_only_diagnostic':{'users':len(warm),**mean(warm)}}


def cohort_hash(cohort):
    canonical=[(u,sorted(cohort['actual'][u]),sorted(cohort['seen'][u])) for u in sorted(cohort['actual'])]
    return hashlib.sha256(json.dumps(canonical,separators=(',',':')).encode()).hexdigest()


def secondary_cohort(history,holdout,primary,cap=5000):
    from pyspark.sql import functions as F
    pairs=history.select('visitorid','itemid').distinct()
    targets=holdout.select('visitorid','itemid').distinct().join(pairs,['visitorid','itemid'],'left_anti').cache()
    excluded=history.sparkSession.createDataFrame([(u,) for u in primary['actual']],'visitorid int')
    users=(targets.select('visitorid').distinct().join(excluded,'visitorid','left_anti')
           .orderBy(F.xxhash64('visitorid',F.lit(43)),'visitorid').limit(cap).cache())
    actual={};seen={}
    for r in targets.join(users,'visitorid').collect():actual.setdefault(int(r.visitorid),set()).add(int(r.itemid))
    seen={u:set() for u in actual}
    for r in pairs.join(users,'visitorid').collect():seen[int(r.visitorid)].add(int(r.itemid))
    all_count=holdout.select('visitorid','itemid').distinct().join(users,'visitorid').count()
    catalog=pairs.select('itemid').distinct()
    base={**primary['base'],'eligible_users':len(actual),'heldout_unique_pairs':all_count,
          'novel_heldout_pairs':sum(map(len,actual.values())),
          'excluded_repeat_pairs':all_count-sum(map(len,actual.values())),
          'cold_users':sum(not s for s in seen.values()),
          'cold_item_pairs':targets.join(users,'visitorid').join(catalog,'itemid','left_anti').count(),
          'evaluation_sampling':'lowest xxhash64(int visitorid,43), cap 5000; exclude all primary IDs; same time period',
          'excluded_primary_users':len(primary['actual'])}
    targets.unpersist()
    assert not set(actual)&set(primary['actual'])
    return {'actual':actual,'seen':seen,'users':users,'base':base}


def fit_neighborhoods(history,cutoff,users,graphs,artifact_root=None):
    """Fit targeted neighborhood scores from ALL past training users.

    Query seed restriction is an exact computation shortcut for this sparse
    model, uses only past query history, never future labels. Cap training users
    at their last 20 distinct items to bound quadratic cost; record this choice.
    """
    from pyspark.sql import functions as F,Window
    spark=history.sparkSession
    past=history.filter(F.col('timestamp')<=cutoff)
    latest=past.groupBy('visitorid','itemid').agg(F.max('timestamp').alias('latest'))
    order=Window.partitionBy('visitorid').orderBy(F.desc('latest'),F.asc('itemid'))
    capped=(latest.withColumn('position',F.row_number().over(order)).filter(F.col('position')<=20).cache())
    seeds=(capped.join(users,'visitorid').filter(F.col('position')<=5)
           .select('visitorid',F.col('itemid').alias('seed'),F.pow(F.lit(.8),F.col('position')-1).alias('affinity')).cache())
    if not seeds.take(1):
        capped.unpersist();seeds.unpersist()
        return {graph_key(g):{} for g in graphs}
    seed_ids=seeds.select(F.col('seed').alias('itemid')).distinct()
    anchors=capped.join(seed_ids,'itemid').select('visitorid',F.col('itemid').alias('seed'))
    candidates=capped.select('visitorid',F.col('itemid').alias('candidate'))
    co=(anchors.join(candidates,'visitorid').filter(F.col('seed')!=F.col('candidate'))
        .groupBy('seed','candidate').count().withColumnRenamed('count','cooccurrence'))
    frequencies=capped.groupBy('itemid').count()
    counts=(co.join(frequencies.select(F.col('itemid').alias('seed'),F.col('count').alias('seed_count')),'seed')
            .join(frequencies.select(F.col('itemid').alias('candidate'),F.col('count').alias('candidate_count')),'candidate').cache())
    pair_count=counts.count()
    history_seen=past.select('visitorid','itemid').distinct().join(users,'visitorid').cache()
    if artifact_root:
        artifact_root=Path(artifact_root)
        counts.write.parquet(str(artifact_root/'targeted_cooccurrence'))
    result={}
    for graph in graphs:
        chosen=counts.filter(F.col('cooccurrence')>=graph['min_cooccurrence'])
        denominator=(F.sqrt(F.col('seed_count')*F.col('candidate_count')) if graph['similarity']=='cosine'
                     else F.col('seed_count')+F.col('candidate_count')-F.col('cooccurrence'))
        chosen=chosen.withColumn('similarity',F.col('cooccurrence')/denominator*F.col('cooccurrence')/(F.col('cooccurrence')+3))
        by_seed=Window.partitionBy('seed').orderBy(F.desc('similarity'),F.asc('candidate'))
        neighbors=chosen.withColumn('neighbor_rank',F.row_number().over(by_seed)).filter(F.col('neighbor_rank')<=100)
        scored=(seeds.join(neighbors,'seed').groupBy('visitorid','candidate')
                .agg(F.sum(F.col('affinity')*F.col('similarity')).alias('score'))
                .withColumnRenamed('candidate','itemid').join(history_seen,['visitorid','itemid'],'left_anti'))
        by_user=Window.partitionBy('visitorid').orderBy(F.desc('score'),F.asc('itemid'))
        rows=(scored.withColumn('rank',F.row_number().over(by_user)).filter(F.col('rank')<=100)
              .orderBy('visitorid','rank').collect())
        values={}
        for row in rows:values.setdefault(int(row.visitorid),[]).append(int(row.itemid))
        result[graph_key(graph)]=values
        print(f'NEIGHBORS {graph_key(graph)}: {len(values)} users, {pair_count} fitted targeted edges',flush=True)
    counts.unpersist();history_seen.unpersist();seeds.unpersist();capped.unpersist()
    return result


def graph_key(graph):return f"{graph['similarity']}-min{graph['min_cooccurrence']}"


def predict(cohort,als,neighborhoods,popular,config,k=10):
    graph=config.get('graph')
    neighbors=neighborhoods.get(graph_key(graph),{}) if graph else {}
    return {u:[r['itemid'] for r in rank_candidates(als.get(u,[]),neighbors.get(u,[]),popular,seen,config,k)]
            for u,seen in cohort['seen'].items()}


def paired_bootstrap(cohort,selected,baseline,iterations=1000):
    import numpy as np
    users=sorted(cohort['actual']); a=user_values(cohort,selected);b=user_values(cohort,baseline)
    differences=np.array([[a[u][key]-b[u][key] for key in METRICS] for u in users])
    rng=np.random.default_rng(42)
    draws=np.empty((iterations,len(METRICS)))
    for i in range(iterations):draws[i]=differences[rng.integers(0,len(users),len(users))].mean(axis=0)
    return {key:{'mean_difference':float(differences[:,j].mean()),
                 'percentile_95_interval':list(map(float,np.quantile(draws[:,j],[.025,.975])))} for j,key in enumerate(METRICS)}


def selection_order(row):
    return (row['minimum_validation_ratio'],row['mean_ndcg_at_k'],row['mean_recall_at_k'],row['mean_precision_at_k'])


def describe_model(metrics,plan,reference,configuration):
    """Keep active model features distinct from the inherited ALS reference."""
    metrics.update(als_reference_parameters=reference['selected_parameters'],
        als_reference_cross_validation=reference['cross_validation'],
        selected_parameters={**plan['fixed_graph'],**configuration},
        cross_validation=metrics['policy_validation']['all_candidates'],
        training_configuration={'source_input_events':2755641,'graph':plan['fixed_graph'],
                                'als_reference_reused_for_comparison':True,
                                'active_als_weight':configuration['weights']['als']},
        confidence_transform='Binary visitor-item cooccurrence; recent seed affinity 0.8^(position-1); similarity shrinkage',
        event_weights={'view':1,'addtocart':1,'transaction':1},
        feature_pipeline={'inputs':['visitorid','itemid','timestamp'],
            'graph':'latest 20 distinct items per training user; cosine/Jaccard with cooccurrence shrinkage',
            'query':'latest 5 distinct past items, top100 neighbors per seed, unseen filter, RRF',
            'als':'Only a comparison or an optional component when active_als_weight > 0'},
        serving_refit_scope='Fit item neighborhoods and 7-day unique-visitor popularity from the full source AFTER frozen evaluation; graph truncates each user to 20 latest distinct items',
        selection_metric='minimum of three mean-validation ratios to popularity-7d baseline; then NDCG, recall, precision')
    metrics.pop('quick_mode',None)
    return metrics


def evaluate_frozen(cohort,als,neighbors,popular,configuration,root,label):
    baseline_configuration={'graph':None,'weights':{'sar':0.,'als':0.,'popularity':1.}}
    predictions=predict(cohort,als,neighbors,popular,configuration)
    baseline=predict(cohort,als,neighbors,popular,baseline_configuration)
    result={'selected':score_predictions(cohort,predictions),'popularity_baseline':score_predictions(cohort,baseline),
            'paired_user_bootstrap_95':paired_bootstrap(cohort,predictions,baseline),
            'cohort_sha256':cohort_hash(cohort),'evaluated_at_utc':utc_now()}
    result['beats_baseline_all_three']=all(result['selected'][key]>result['popularity_baseline'][key] for key in METRICS)
    atomic_json(root/f'metrics/{label}_comparison.json',result)
    with (root/f'metrics/{label}_rankings.jsonl').open('w',encoding='utf-8') as sink:
        for user in sorted(cohort['actual']):
            sink.write(json.dumps({'visitorid':user,'actual':sorted(cohort['actual'][user]),'seen':sorted(cohort['seen'][user]),
                                  'selected':predictions[user],'baseline':baseline[user]},separators=(',',':'))+'\n')
    return result


def execute(source_run,run_id,output_root='outputs'):
    os.environ.setdefault('PYSPARK_SUBMIT_ARGS','--driver-memory 3g --conf spark.driver.maxResultSize=512m --conf spark.local.dir=/tmp/neighborhood_scratch pyspark-shell')
    os.environ['BIGDATA_LOG_CONFIDENCE']='1';os.environ['BIGDATA_ALS_BLOCKS']='6'
    root=run_path(run_id,output_root);source=run_path(source_run,output_root)
    if root.exists():raise FileExistsError('Choose a new run ID')
    plan=json.loads(Path('docs/NEIGHBORHOOD_EXPERIMENT_PLAN.json').read_text())
    plan={**plan,'template_registered_at_utc':plan['registered_at_utc'],'registered_at_utc':utc_now(),'source_run':source_run,'run_id':run_id}
    source_report=json.loads((source/'metrics/run_report.json').read_text())
    split=json.loads((source/'metrics/temporal_split.json').read_text())
    old=json.loads((source/'metrics/model_metrics.json').read_text())
    assert source_report['status']=='completed' and split['input_events']==2755641
    baseline_config={'graph':None,'weights':{'sar':0.,'als':0.,'popularity':1.}}
    als_config={'graph':None,'weights':{'sar':0.,'als':1.,'popularity':0.}}
    configs=[baseline_config,als_config]+[{'graph':g,'weights':w} for g in plan['candidate_graphs'] for w in plan['candidate_weights']]
    report={'run_id':run_id,'source_run_id':source_run,'status':'running','started_at_utc':utc_now(),'clean_event_count':2755641,
            'scope':'Full-history offline item-neighborhood experiment; model selection is validation-only; test gates activation',
            'stages':{key:source_report['stages'][key] for key in ('history_import','lake','acid')}}
    report['stages']['acid']={**report['stages']['acid'],'reused_from_run_id':source_run}
    spark=None;start=time.monotonic()
    with pipeline_lock(output_root):
        def stage(name):
            report['current_stage']=name;atomic_json(root/'metrics/run_report.json',report)
            print(f'[{utc_now()}] NEIGHBORHOOD: {name}',flush=True)
        try:
            from pyspark.sql import functions as F
            from pyspark.ml import PipelineModel
            from phase3.streaming import create_spark
            from delta.tables import DeltaTable
            atomic_json(root/'metrics/experiment_plan.json',plan)
            stage('read_frozen_full_source')
            spark=create_spark('local[4]',enable_delta=True,download_jars=True)
            spark.conf.set('spark.sql.shuffle.partitions','24');spark.sparkContext.setCheckpointDir('/tmp/neighborhood_checkpoints')
            assert int(DeltaTable.forPath(spark,split['source']).history(1).first().version)==split['delta_version']
            events=(spark.read.format('delta').option('versionAsOf',split['delta_version']).load(split['source'])
                    .selectExpr('timestamp','cast(visitorid as int) visitorid','cast(itemid as int) itemid','event').cache())
            assert events.count()==2755641
            folds=[]
            for index,fold in enumerate(rolling_folds(split['boundaries']),1):
                stage(f'validation_fold_{index}')
                cutoff=fold['train_end_inclusive'];history=events.filter(F.col('timestamp')<=cutoff).cache()
                future=events.filter((F.col('timestamp')>cutoff)&(F.col('timestamp')<=fold['validation_end_inclusive']))
                cohort=make_cohort(history,future)
                neighbors=fit_neighborhoods(history,cutoff,cohort['users'],plan['candidate_graphs'])
                interactions=prepare_interactions(history).cache()
                fitted=fit_als_pipeline(interactions,old['selected_parameters'],max_iter=4)
                als=als_lists(fitted.stages[-1],history,cohort['users'],cohort['base']['training_catalog_size'])
                pop=popularity(history,cutoff,7,max(2000,max(map(len,cohort['seen'].values()))+1000))
                scores=[score_predictions(cohort,predict(cohort,als,neighbors,pop,config)) for config in configs]
                evidence={'fold':fold,'cohort_sha256':cohort_hash(cohort),'results':[{'configuration':c,'metrics':m} for c,m in zip(configs,scores)]}
                atomic_json(root/f'metrics/validation_fold_{index}.json',evidence);folds.append(evidence)
                fitted.stages[-1].userFactors.unpersist();fitted.stages[-1].itemFactors.unpersist()
                cohort['users'].unpersist();history.unpersist();interactions.unpersist()
                del fitted,cohort,als,neighbors
                gc.collect();spark._jvm.java.lang.System.gc()
                print(f'VALIDATION FOLD {index} COMMITTED',flush=True)
            candidates=[]
            baseline_means={key:sum(f['results'][0]['metrics'][key] for f in folds)/len(folds) for key in METRICS}
            for index,config in enumerate(configs):
                metrics=[f['results'][index]['metrics'] for f in folds]
                means={key:sum(m[key] for m in metrics)/len(metrics) for key in METRICS}
                ratios={key:means[key]/baseline_means[key] if baseline_means[key] else (1. if means[key]==0 else 1e9) for key in METRICS}
                candidates.append({'configuration':config,'folds':metrics,**{'mean_'+k:v for k,v in means.items()},
                                   'ratios_to_baseline':ratios,'minimum_validation_ratio':min(ratios.values())})
            selected=max(candidates,key=selection_order)
            selection={'selected':selected,'popularity_baseline':candidates[0],'all_candidates':candidates,'selected_at_utc':utc_now(),
                       'selection_rule':'minimum_validation_ratio_then_ndcg_recall_precision','source':split,
                       'selection_scope':'Two historical validation folds only; all candidates fixed by pre-registered plan',
                       'test_reuse_disclosure':'Primary test already reported before; secondary user cohort disjoint, same period, not fresh future data'}
            atomic_json(root/'metrics/policy_selection.json',selection)
            print('FROZEN SELECTION: '+json.dumps(selected),flush=True)
            if selected['minimum_validation_ratio']<=1:
                report.update(status='completed_not_promoted',promotion_gate_passed=False,
                              reason='No strict validation improvement on all three metrics; test not evaluated.',
                              finished_at_utc=utc_now(),elapsed_seconds=round(time.monotonic()-start,3))
                atomic_json(root/'metrics/run_report.json',report)
                return report
            stage('evaluate_frozen_primary_and_disjoint_secondary_once')
            cutoff=split['boundaries'][-1];history=events.filter(F.col('timestamp')<=cutoff).cache()
            future=events.filter(F.col('timestamp')>cutoff)
            primary=make_cohort(history,future)
            secondary=secondary_cohort(history,future,primary)
            users=primary['users'].unionByName(secondary['users']).distinct().cache()
            graph=selected['configuration'].get('graph')
            neighbors=fit_neighborhoods(history,cutoff,users,[graph] if graph else []) if graph else {}
            fitted=PipelineModel.load(str(source/'models/evaluation_pipeline'))
            als=als_lists(fitted.stages[-1],history,users,primary['base']['training_catalog_size'])
            limit=max(2000,max(len(s) for c in (primary,secondary) for s in c['seen'].values())+1000)
            pop=popularity(history,cutoff,7,limit)
            first=evaluate_frozen(primary,als,neighbors,pop,selected['configuration'],root,'primary_test')
            second=evaluate_frozen(secondary,als,neighbors,pop,selected['configuration'],root,'secondary_test')
            primary_als=score_predictions(primary,{u:als.get(u,[])[:10] for u in primary['actual']})
            for key in METRICS:assert abs(primary_als[key]-old['heldout_test'][key])<1e-10
            passed=selected['minimum_validation_ratio']>1 and first['beats_baseline_all_three']
            report['promotion_gate_passed']=passed
            atomic_json(root/'metrics/quality_gate.json',{'passed':passed,'validation_minimum_ratio':selected['minimum_validation_ratio'],
                        'primary_beats_all_three':first['beats_baseline_all_three'],'secondary_beats_all_three':second['beats_baseline_all_three'],
                        'rule':plan['promotion_gate'],'primary_als_reproduced':primary_als,'checked_at_utc':utc_now()})
            print('FROZEN PRIMARY: '+json.dumps(first),flush=True)
            print('FROZEN SECONDARY: '+json.dumps(second),flush=True)
            fitted.stages[-1].userFactors.unpersist();fitted.stages[-1].itemFactors.unpersist()
            primary['users'].unpersist();secondary['users'].unpersist();users.unpersist();history.unpersist()
            del fitted,primary,secondary,neighbors,als
            gc.collect();spark._jvm.java.lang.System.gc()
            metrics={**old,'run_id':run_id,'source_run_id':source_run,'algorithm':'item_knn_hybrid',
                     'heldout_test':first['selected'],'selected_policy':selected['configuration'],'policy_validation':selection,
                     'quality_comparison':{'previous_reported_als':old['heldout_test'],'als_recomputed_same_cohort':primary_als,
                                           'popularity_baseline':first['popularity_baseline'],'selected_policy':first['selected']},
                     'secondary_test':second,'paired_primary_bootstrap_95':first['paired_user_bootstrap_95'],
                     'metric_model_version':f'{run_id}:neighborhood-evaluation-v1','serving_model_version':f'{run_id}:neighborhood-serving-v1',
                     'evaluated_at_utc':first['evaluated_at_utc'],'promotion_gate_passed':passed,
                     'modeling_scope':plan['fixed_graph'],'quality_gate':plan['promotion_gate'],
                     'training_note':'Item-item graph learned from all past users with last-20-items cap; past query seeds only; reuse chronological ALS factors; all cold users retained.'}
            describe_model(metrics,plan,old,selected['configuration'])
            atomic_json(root/'metrics/model_metrics.json',metrics)
            atomic_json(root/'metrics/temporal_split.json',split)
            kpis=json.loads((source/'metrics/kpis.json').read_text());kpis['run_id']=run_id;atomic_json(root/'metrics/kpis.json',kpis)
            if not passed:
                report.update(status='completed_not_promoted',finished_at_utc=utc_now(),elapsed_seconds=round(time.monotonic()-start,3))
                report['stages']['training']=metrics;atomic_json(root/'metrics/run_report.json',report)
                return report
            stage('fit_selected_full_history_neighborhood_and_export_serving')
            old_bundle=json.loads((source/'serving/model_bundle.json').read_text());ids=sorted(int(u) for u in old_bundle['recommendations'])
            with closing(sqlite3.connect(source/'serving/seen_items.sqlite')) as db:
                seen={u:{int(r[0]) for r in db.execute('SELECT itemid FROM seen_items WHERE visitorid=?',(u,))} for u in ids}
            users=spark.createDataFrame([(u,) for u in ids],'visitorid int');full_end=events.agg(F.max('timestamp')).first()[0]
            neighbors=fit_neighborhoods(events,full_end,users,[graph] if graph else [],root/'models') if graph else {}
            model=PipelineModel.load(str(source/'models/als_pipeline'))
            als=als_lists(model.stages[-1],events,users,old['serving_scope']['training_catalog_size'])
            pop=popularity(events,full_end,7,max(2000,max(map(len,seen.values()))+1000))
            config=selected['configuration'];neighbor_values=neighbors.get(graph_key(graph),{}) if graph else {}
            exported={str(u):rank_candidates(als.get(u,[]),neighbor_values.get(u,[]),pop,seen[u],config,100) for u in ids}
            serving=root/'serving';serving.mkdir(parents=True,exist_ok=True)
            shutil.copy2(source/'serving/seen_items.sqlite',serving/'seen_items.sqlite')
            metrics.update(trained_at_utc=utc_now(),serving_scope={**old['serving_scope'],'exported_recommendations_per_user':100,
                'fallback':'unique-visitor popularity in last 7 days; all observed seen filtered',
                'all_recommendations_parquet':'models/recommendations','reused_als_factors_from':source_run,
                'neighborhood_scope':'past last-20 distinct items per training user; targeted graph for past query seeds; top100 neighbors'})
            atomic_json(root/'metrics/model_metrics.json',metrics)
            rows=[(int(u),r['itemid'],r['score'],rank) for u,items in exported.items() for rank,r in enumerate(items,1)]
            spark.createDataFrame(rows,'visitorid int,itemid int,score double,rank int').write.parquet(str(root/'models/recommendations'))
            bundle={'run_id':run_id,'model_version':metrics['serving_model_version'],'algorithm':'item_knn_hybrid',
                    'trained_at_utc':metrics['trained_at_utc'],'recommendations':exported,'seen_items':{str(u):sorted(seen[u]) for u in ids},
                    'seen_items_store':'seen_items.sqlite','popular_items':[{'itemid':item,'score':1/(60+rank)} for rank,item in enumerate(pop,1)],
                    'metrics':metrics,'serving_scope':metrics['serving_scope']}
            from service.store import validate_bundle
            validate_bundle(bundle,run_id);atomic_json(serving/'model_bundle.json',bundle)
            from pipeline.runner import publish_model_registry
            report['stages']['training']=metrics
            stage('publish_registry_and_promote_validated_model')
            report['stages']['model_registry']=publish_model_registry(root,run_id,os.environ['MONGO_URI'],os.environ['REDIS_URL'])
            report.update(status='completed',finished_at_utc=utc_now(),elapsed_seconds=round(time.monotonic()-start,3))
            atomic_json(root/'metrics/run_report.json',report)
            atomic_json(Path(output_root)/'latest_run.json',{'run_id':run_id,'model_version':metrics['serving_model_version'],'activated_at_utc':utc_now()})
            return report
        except Exception as error:
            report.update(status='failed',error=f'{type(error).__name__}: {error}',finished_at_utc=utc_now())
            atomic_json(root/'metrics/run_report.json',report);raise
        finally:
            if spark:spark.stop()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-run',required=True);parser.add_argument('--run-id',required=True)
    parser.add_argument('--output-root',default=os.getenv('OUTPUT_ROOT','outputs'))
    args=parser.parse_args();report=execute(args.source_run,args.run_id,args.output_root)
    print(json.dumps({'run_id':report['run_id'],'status':report['status'],'promotion_gate_passed':report.get('promotion_gate_passed')},indent=2))
