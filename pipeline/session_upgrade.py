"""Validation-selected weighted item-item and separate next-new-item session study.

Read the immutable full Delta snapshot once, then use bounded sparse matrices.
All hyperparameters are registered before validation. Test never selects a policy.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import gc
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import time

from pipeline.config import atomic_json, pipeline_lock, run_path, utc_now
from pipeline.ranking import user_ranking_metrics, rolling_folds
from pipeline.session_model import context_affinities, rank_context

METRICS = ('precision_at_k', 'recall_at_k', 'ndcg_at_k')
EVENTS = ('view', 'addtocart', 'transaction')
CURRENT = {'name': 'current_item_item', 'graph': 'binary_user', 'event_weights': dict.fromkeys(EVENTS, 1),
           'query_half_life_minutes': None, 'position_decay': .8, 'seed_limit': 5, 'popularity_weight': 0}
POLICIES = [
    {'name': 'weighted_user', 'graph': 'weighted_user'},
    {'name': 'weighted_session', 'graph': 'weighted_session'},
    {'name': 'weighted_session_pop20', 'graph': 'weighted_session', 'popularity_weight': .2},
]


def configuration(policy, protocol):
    return {'event_weights': {'view': 1, 'addtocart': 3, 'transaction': 5},
            'query_half_life_minutes': 30 if protocol == 'session' else 30*24*60,
            'position_decay': .8, 'seed_limit': 5, 'popularity_weight': 0, **policy}


def plan_document(source, reference, run_id):
    return {'registered_at_utc': utc_now(), 'source_run_id': source, 'reference_run_id': reference, 'run_id': run_id,
            'k': 10, 'candidate_policies': POLICIES, 'current_policy': CURRENT,
            'training': {'max_distinct_items_per_entity': 20, 'neighbor_limit': 100, 'shrinkage': 3,
                         'weighted_similarity': 'cosine of max(event_weight * 2^(-age/30days)) per entity-item',
                         'binary_similarity': 'Jaccard, identical to frozen current item-item',
                         'entity': 'visitor for user graph; visitor session separated by >30min gap for session graph',
                         'all_training_events_read': True},
            'session_protocol': {'gap_minutes': 30, 'sample_cap': 5000, 'sample_seed': 42,
                'query': 'first next NEW item per eligible session, with >=1 strictly earlier distinct context item; one target; ties choose lowest item ID',
                'sampling': 'lowest SHA256(visitor:session_start:42), tie by query_id',
                'context': 'last 100 events strictly before target, up to 5 distinct latest seeds; exclude all earlier items in that session',
                'cold_targets': 'keep unseen catalog targets as misses; no sampled negatives; bounded retrieval top100 neighbors/seed and top100 combined scores, popularity pool10000',
                'inference': 'frozen graph <= global train cutoff; past context after cutoff allowed, never updates graph',
                'scope': 'conditional next-new-item protocol; no-history first events are ineligible and counted; not comparable to static macro-user metrics'},
            'selection': 'separate static/session champions: maximum mean NDCG across two chronological validation folds, then recall, precision, name; no test tuning',
            'static_promotion': 'strict improvement over current on all 3 mean validation metrics AND all 3 frozen primary-test metrics; otherwise preserve static recommendations',
            'session_promotion': 'strict improvement over context-aware current item-item on all 3 validation metrics AND all 3 session-test metrics; otherwise serve current graph with session context',
            'uncertainty': '1000 paired visitor-cluster bootstrap draws seed42; per-metric percentile95 intervals',
            'test_reuse_disclosure': 'Static primary was already inspected during earlier work; new session protocol on same final time window is not a fresh future dataset.'}


def strictly_better(candidate, baseline):
    """Equal hit totals must not pass a gate because of float summation noise."""
    return all(candidate[m] > baseline[m] + 1e-12 for m in METRICS)


def canonical_hash(rows):
    keys = ('query_id','visitorid','actual','seen','context','as_of','target_timestamp')
    values = [{k: (sorted(r[k]) if k in ('actual','seen') else r[k]) for k in keys} for r in rows]
    values.sort(key=lambda r:r['query_id'])
    return hashlib.sha256(json.dumps(values,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()).hexdigest()


def metric_values(rows, name):
    return [user_ranking_metrics(r[name], r['actual'], 10) for r in rows]


def summarize(rows, name):
    values = metric_values(rows,name)
    return {'k':10,'queries':len(rows), **{m:sum(v[m] for v in values)/len(values) if values else 0. for m in METRICS},
            'total_hits':sum(len(set(r[name]) & set(r['actual'])) for r in rows),
            'hit_queries':sum(bool(set(r[name]) & set(r['actual'])) for r in rows),
            'recommendation_coverage':sum(bool(r[name]) for r in rows)/len(rows) if rows else 0.}


def clustered_bootstrap(rows, a='selected', b='current_item_item'):
    import numpy as np
    clusters = {}
    for row, av, bv in zip(rows,metric_values(rows,a),metric_values(rows,b)):
        key=row['visitorid']
        if key not in clusters:clusters[key]=[np.zeros(3),0]
        clusters[key][0]+=np.array([av[m]-bv[m] for m in METRICS]);clusters[key][1]+=1
    sums=np.array([x[0] for x in clusters.values()]);counts=np.array([x[1] for x in clusters.values()])
    rng=np.random.default_rng(42);draws=np.empty((1000,3))
    for i in range(1000):
        ids=rng.integers(0,len(counts),len(counts));draws[i]=sums[ids].sum(axis=0)/counts[ids].sum()
    return {'unit':'visitor clusters; query-weighted macro metrics','visitors':len(counts),'iterations':1000,
            'metrics':{m:{'mean_difference':float(sums[:,j].sum()/counts.sum()),
                           'percentile_95_interval':list(map(float,np.quantile(draws[:,j],[.025,.975])))} for j,m in enumerate(METRICS)}}


def extract_delta(source, root, split):
    import numpy as np
    from phase3.streaming import create_spark
    from pyspark.sql import functions as F
    from delta.tables import DeltaTable
    from pipeline.hybrid_upgrade import make_cohort
    spark=create_spark('local[4]',enable_delta=True,download_jars=True)
    spark.conf.set('spark.sql.shuffle.partitions','24')
    try:
        assert int(DeltaTable.forPath(spark,split['source']).history(1).first().version)==split['delta_version']==0
        frame=(spark.read.format('delta').option('versionAsOf',split['delta_version']).load(split['source'])
               .selectExpr('timestamp','cast(visitorid as int) visitorid','cast(itemid as int) itemid','event').cache())
        count=frame.count();assert count==2755641
        cohorts=[]
        for index,fold in enumerate(rolling_folds(split['boundaries']),1):
            cutoff=fold['train_end_inclusive']
            cohort=make_cohort(frame.filter(F.col('timestamp')<=cutoff),frame.filter((F.col('timestamp')>cutoff)&(F.col('timestamp')<=fold['validation_end_inclusive'])))
            value={'fold':fold,'actual':{str(u):sorted(a) for u,a in cohort['actual'].items()},
                   'seen':{str(u):sorted(a) for u,a in cohort['seen'].items()},'base':cohort['base']}
            atomic_json(root/f'metrics/static_validation_cohort_{index}.json',value);cohorts.append(value)
            cohort['users'].unpersist();print(f'EXTRACTED STATIC COHORT {index}',flush=True)
        arrays={'timestamp':np.empty(count,np.int64),'visitorid':np.empty(count,np.int32),
                'itemid':np.empty(count,np.int32),'event':np.empty(count,np.int8)}
        codes={e:i for i,e in enumerate(EVENTS)}
        total=0
        for row in frame.toLocalIterator():
            arrays['timestamp'][total]=row.timestamp;arrays['visitorid'][total]=row.visitorid
            arrays['itemid'][total]=row.itemid;arrays['event'][total]=codes[row.event];total+=1
        assert total==count
        (root/'data').mkdir(exist_ok=True)
        np.savez_compressed(root/'data/frozen_events.npz',**arrays)
        atomic_json(root/'metrics/source_snapshot.json',{'source_run_id':source.name,'source':split['source'],
            'delta_version':0,'input_events':count,'extracted_at_utc':utc_now(),
            'npz_sha256':hashlib.sha256((root/'data/frozen_events.npz').read_bytes()).hexdigest()})
        return arrays,cohorts
    finally:spark.stop()


class EventData:
    def __init__(self,arrays):
        import numpy as np
        order=np.lexsort((arrays['event'],arrays['itemid'],arrays['timestamp'],arrays['visitorid']))
        self.a={k:v[order] for k,v in arrays.items()}
        u,t=self.a['visitorid'],self.a['timestamp']
        new=np.r_[True,(u[1:]!=u[:-1]) | (t[1:]-t[:-1]>1800000)]
        self.sessions=np.cumsum(new,dtype=np.int32)-1
        self.starts=np.flatnonzero(new);self.ends=np.r_[self.starts[1:],len(t)]
        self.catalog_columns=int(self.a['itemid'].max())+1

    def static_rows(self,cohort,cutoff,end):
        import numpy as np
        ids=np.array([int(u) for u in cohort['actual']],np.int32)
        chosen=np.flatnonzero((self.a['timestamp']<=cutoff)&np.isin(self.a['visitorid'],ids))
        histories={int(u):[] for u in ids}
        for i in chosen:
            histories[int(self.a['visitorid'][i])].append(self.event(int(i)))
        first_targets={}
        future=np.flatnonzero((self.a['timestamp']>cutoff)&(self.a['timestamp']<=end)&np.isin(self.a['visitorid'],ids))
        targets={int(u):set(a) for u,a in cohort['actual'].items()}
        for i in future:
            user=int(self.a['visitorid'][i])
            if user not in first_targets and int(self.a['itemid'][i]) in targets[user]:
                first_targets[user]=int(self.a['timestamp'][i])
        rows=[]
        for user in sorted(histories):
            # Keep all historical events for exact top-five timestamp and max-strength aggregation.
            rows.append({'query_id':str(user),'visitorid':user,'actual':cohort['actual'][str(user)],
                         'seen':cohort['seen'][str(user)],'context':histories[user],
                         'as_of':int(cutoff),'target_timestamp':first_targets.get(user,int(cutoff+1))})
        return rows

    def event(self,index):
        return {'itemid':int(self.a['itemid'][index]),'event':EVENTS[int(self.a['event'][index])],
                'timestamp':int(self.a['timestamp'][index])}

    def session_rows(self,cutoff,end,cap=5000):
        import heapq
        t=self.a['timestamp'];items=self.a['itemid'];users=self.a['visitorid']
        selected=[];active=eligible=repeat_events=without_context=0
        for start,stop in zip(self.starts,self.ends):
            start=int(start);stop=int(stop)
            if t[stop-1]<=cutoff or t[start]>end:continue
            active+=1;seen=set();context=[];index=start;found=None
            while index<stop and t[index]<=end:
                timestamp=int(t[index]);endgroup=index+1
                while endgroup<stop and t[endgroup]==timestamp:endgroup+=1
                novel=sorted({int(i) for i in items[index:endgroup]}-seen)
                if timestamp>cutoff:
                    repeat_events+=sum(int(i) in seen for i in items[index:endgroup])
                    if context and novel:
                        user=int(users[start]);key=f'{user}:{int(t[start])}'
                        found={'query_id':key,'visitorid':user,'actual':[novel[0]],'seen':sorted(seen),
                               'context':context[-100:],'as_of':timestamp-1,'target_timestamp':timestamp}
                        break
                    if not context:without_context+=1
                for i in range(index,endgroup):
                    context.append(self.event(i));seen.add(int(items[i]))
                index=endgroup
            if found:
                eligible+=1
                digest=hashlib.sha256((found['query_id']+':42').encode()).hexdigest()
                selected.append((digest,found['query_id'],found))
        rows=[v[2] for v in heapq.nsmallest(cap,selected,key=lambda v:(v[0],v[1]))]
        rows.sort(key=lambda r:r['query_id'])
        return rows,{'active_sessions':active,'eligible_sessions':eligible,'sampled_queries':len(rows),
                     'ineligible_sessions':active-eligible,'first_timestamp_groups_without_context':without_context,
                     'repeated_events_before_first_query':repeat_events,'unique_visitors':len({r['visitorid'] for r in rows}),
                     'sampling_uses_predictions_or_target_identity':False}

    def popularity(self,cutoff):
        import numpy as np
        take=(self.a['timestamp']<=cutoff)&(self.a['timestamp']>cutoff-7*86400000)
        pair=np.rec.fromarrays([self.a['visitorid'][take],self.a['itemid'][take]],names='u,i')
        unique=np.unique(pair)
        ids,counts=np.unique(unique.i,return_counts=True)
        order=np.lexsort((ids,-counts))
        return [int(i) for i in ids[order][:10000]]


class SparseGraph:
    def __init__(self,data,cutoff,kind,max_items_per_entity=20,neighbor_limit=100,
                 binary_similarity='jaccard',shrinkage=3):
        import numpy as np
        from scipy.sparse import csr_matrix
        take=data.a['timestamp']<=cutoff
        entities=(data.sessions if kind=='weighted_session' else data.a['visitorid'])[take]
        items=data.a['itemid'][take];stamps=data.a['timestamp'][take];event=data.a['event'][take]
        weights=np.ones(len(items),np.float64) if kind=='binary_user' else np.array([1.,3.,5.])[event]*np.exp2(-(cutoff-stamps)/(30*86400000))
        order=np.lexsort((items,entities));entities=entities[order];items=items[order];stamps=stamps[order];weights=weights[order]
        starts=np.r_[0,np.flatnonzero((entities[1:]!=entities[:-1])|(items[1:]!=items[:-1]))+1]
        latest=np.maximum.reduceat(stamps,starts);strength=np.maximum.reduceat(weights,starts)
        entities=entities[starts];items=items[starts];pair_count=len(items)
        order=np.lexsort((items,-latest,entities));entities=entities[order];items=items[order];strength=strength[order]
        starts=np.r_[0,np.flatnonzero(entities[1:]!=entities[:-1])+1]
        positions=np.arange(len(entities))-np.repeat(starts,np.diff(np.r_[starts,len(entities)]))
        keep=positions<max_items_per_entity
        self.matrix=csr_matrix((strength[keep],(entities[keep],items[keep])),shape=(int(entities.max())+1,data.catalog_columns))
        self.binary=self.matrix.copy();self.binary.data[:]=1.
        self.transpose=self.matrix.T.tocsr();self.binary_transpose=self.binary.T.tocsr()
        self.kind=kind;self.cutoff=int(cutoff);self.cache={};self.neighbor_limit=neighbor_limit
        self.binary_similarity=binary_similarity;self.shrinkage=shrinkage
        self.norm=np.asarray(self.matrix.power(2).sum(axis=0)).ravel()
        self.frequency=np.asarray(self.binary.sum(axis=0)).ravel()
        self.catalog=np.flatnonzero(self.frequency)
        self.profile={'graph':kind,'train_end_inclusive':int(cutoff),'input_events':int(take.sum()),
                      'entity_item_pairs_before_cap':pair_count,'entity_item_pairs_after_cap':self.matrix.nnz,
                      'entities':len(starts),'catalog_items':len(self.catalog),'half_life_days':None if kind=='binary_user' else 30,
                      'max_items_per_entity':max_items_per_entity,'neighbor_limit':neighbor_limit,
                      'binary_similarity':binary_similarity,'shrinkage':shrinkage}
        print('FIT '+json.dumps(self.profile),flush=True)

    def neighbors(self,seeds):
        import numpy as np
        seeds=sorted(set(int(i) for i in seeds));missing=[i for i in seeds if i not in self.cache]
        for begin in range(0,len(missing),256):
            batch=np.array(missing[begin:begin+256],np.int32)
            valid=batch[(batch>=0)&(batch<self.matrix.shape[1])]
            for seed in batch:self.cache[int(seed)]=[]
            if not len(valid):continue
            counts=(self.binary_transpose[valid]@self.binary).tocsr();counts.sort_indices()
            if self.kind=='binary_user':dots=counts
            else:
                dots=(self.transpose[valid]@self.matrix).tocsr();dots.sort_indices()
                assert np.array_equal(counts.indptr,dots.indptr) and np.array_equal(counts.indices,dots.indices)
            for row,seed in enumerate(valid):
                start,stop=counts.indptr[row:row+2];ids=counts.indices[start:stop];co=counts.data[start:stop]
                if self.kind=='binary_user':
                    if self.binary_similarity=='jaccard':
                        similarity=co/(self.frequency[seed]+self.frequency[ids]-co)
                    elif self.binary_similarity=='cosine':
                        similarity=co/np.sqrt(self.frequency[seed]*self.frequency[ids])
                    elif self.binary_similarity=='conditional':
                        similarity=co/self.frequency[seed]
                    else:raise ValueError(self.binary_similarity)
                else:similarity=dots.data[start:stop]/np.sqrt(self.norm[seed]*self.norm[ids])
                scores=similarity*co/(co+self.shrinkage)
                keep=(ids!=seed)&(scores>0);ids=ids[keep];scores=scores[keep]
                order=np.lexsort((ids,-scores))[:self.neighbor_limit]
                self.cache[int(seed)]=[(int(ids[i]),float(scores[i])) for i in order]
        return {s:self.cache[s] for s in seeds}

    def export(self,path):
        path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
        if path.exists():raise FileExistsError(path)
        rows=0
        with closing(sqlite3.connect(path)) as db:
            db.execute('PRAGMA journal_mode=OFF');db.execute('PRAGMA synchronous=OFF')
            db.execute('CREATE TABLE neighbors(seed INTEGER,candidate INTEGER,score REAL NOT NULL,PRIMARY KEY(seed,candidate)) WITHOUT ROWID')
            db.execute('CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL) WITHOUT ROWID')
            db.execute('INSERT INTO metadata VALUES (?,?)',('training',json.dumps(self.profile)))
            for start in range(0,len(self.catalog),512):
                batch=self.catalog[start:start+512];neighbors=self.neighbors(batch)
                values=[(seed,item,score) for seed,edges in neighbors.items() for item,score in edges]
                db.executemany('INSERT INTO neighbors VALUES (?,?,?)',values);rows+=len(values)
                self.cache.clear()
                if start%10240==0:db.commit();print(f'INDEX {start}/{len(self.catalog)}, edges={rows}',flush=True)
            db.commit()
        return {'rows':rows,'seeds':len(self.catalog),'bytes':path.stat().st_size,
                'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),**self.profile}


def evaluate(data,cutoff,rows,policies,popular,known_current=None):
    configurations=[CURRENT]+policies
    predictions={c['name']:{} for c in configurations};profiles=[]
    for kind in sorted({c['graph'] for c in configurations}):
        graph=SparseGraph(data,cutoff,kind);profiles.append(graph.profile)
        for config in configurations:
            if config['graph']!=kind:continue
            seeds={seed for row in rows for seed,_ in context_affinities(row['context'],config,row['as_of'])}
            neighbors=graph.neighbors(seeds)
            for row in rows:
                result=rank_context(neighbors,row['context'],popular,row['seen'],10,config,row['as_of'])
                predictions[config['name']][row['query_id']]=[r['itemid'] for r in result['items']]
        del graph;gc.collect()
    for row in rows:
        for name,values in predictions.items():row[name]=values[row['query_id']]
        row['popularity']=[i for i in popular if i not in set(row['seen'])][:10]
        if known_current is not None:
            assert row['current_item_item']==known_current[row['visitorid']],f'Current reproduction mismatch {row["visitorid"]}'
    return {name:summarize(rows,name) for name in [c['name'] for c in configurations]+['popularity']},profiles


def comparison(root,label,rows,selected,cutoff,protocol,extra=None):
    for row in rows:row['selected']=row[selected['name']]
    filename=label+'_rankings.jsonl'
    names=('selected','current_item_item','popularity')
    with (root/'metrics'/filename).open('w',encoding='utf-8') as handle:
        for row in rows:
            value={k:v for k,v in row.items() if k in ('query_id','visitorid','actual','seen','context','as_of','target_timestamp')+names}
            handle.write(json.dumps(value,separators=(',',':'))+'\n')
    result={'k':10,'protocol':protocol,'models':{name:summarize(rows,name) for name in names},
            'rankings_file':filename,'cohort_sha256':canonical_hash(rows),'graph_train_end_inclusive':int(cutoff),
            'selected_configuration':selected,'evaluated_at_utc':utc_now(),
            'paired_visitor_cluster_bootstrap_95':clustered_bootstrap(rows),
            'paired_vs_popularity_bootstrap_95':clustered_bootstrap(rows,b='popularity'),**(extra or {})}
    atomic_json(root/f'metrics/{label}_comparison.json',result)
    return result


def execute(source_run,reference_run,run_id,output_root='outputs'):
    source=run_path(source_run,output_root);reference=run_path(reference_run,output_root);root=run_path(run_id,output_root)
    if root.exists():raise FileExistsError('Use a new run ID; committed evidence is never overwritten')
    (root/'metrics').mkdir(parents=True)
    split=json.loads((source/'metrics/temporal_split.json').read_text())
    old_bundle=json.loads((reference/'serving/model_bundle.json').read_text())
    assert old_bundle['algorithm']=='item_knn_hybrid'
    plan=plan_document(source_run,reference_run,run_id);atomic_json(root/'metrics/experiment_plan.json',plan)
    report={'run_id':run_id,'source_run_id':source_run,'source_data_run_id':source_run,'reference_run_id':reference_run,
            'status':'running','started_at_utc':utc_now(),'stages':{}};start=time.monotonic()
    def stage(name):
        report['current_stage']=name;atomic_json(root/'metrics/run_report.json',report);print(f'[{utc_now()}] SESSION {name}',flush=True)
    with pipeline_lock(output_root):
        try:
            stage('extract_immutable_full_delta_and_fixed_validation_cohorts')
            arrays,cohorts=extract_delta(source,root,split);data=EventData(arrays);del arrays;gc.collect()
            validation={'static':[],'session':[]}
            for index,fold in enumerate(rolling_folds(split['boundaries']),1):
                cutoff=fold['train_end_inclusive'];end=fold['validation_end_inclusive'];pop=data.popularity(cutoff)
                for protocol in ('static','session'):
                    stage(f'validation_{protocol}_fold_{index}')
                    if protocol=='static':rows=data.static_rows(cohorts[index-1],cutoff,end);population=cohorts[index-1]['base']
                    else:rows,population=data.session_rows(cutoff,end)
                    configs=[configuration(c,protocol) for c in POLICIES]
                    scores,profiles=evaluate(data,cutoff,rows,configs,pop)
                    evidence={'fold':fold,'protocol':protocol,'cohort_sha256':canonical_hash(rows),'population':population,'scores':scores,'graphs':profiles}
                    atomic_json(root/f'metrics/{protocol}_validation_fold_{index}.json',evidence);validation[protocol].append(evidence)
            stage('freeze_separate_policies_before_final_test')
            selection={'frozen_at_utc':utc_now(),'rule':plan['selection'],'test_reuse_disclosure':plan['test_reuse_disclosure']}
            for protocol,folds in validation.items():
                means={name:{m:sum(f['scores'][name][m] for f in folds)/len(folds) for m in METRICS} for name in folds[0]['scores']}
                candidates=[configuration(c,protocol) for c in POLICIES]
                best=max(candidates,key=lambda c:(means[c['name']]['ndcg_at_k'],means[c['name']]['recall_at_k'],means[c['name']]['precision_at_k'],c['name']))
                beats=strictly_better(means[best['name']],means['current_item_item'])
                selection[protocol]={'selected_configuration':best,'mean_metrics':means,'validation_beats_current_all_three':beats}
            atomic_json(root/'metrics/policy_selection.json',selection)
            print('FROZEN '+json.dumps(selection),flush=True)
            stage('evaluate_frozen_static_and_session_once')
            cutoff=split['boundaries'][-1];end=int(data.a['timestamp'].max());pop=data.popularity(cutoff)
            prior=[json.loads(line) for line in (reference/'metrics/primary_test_rankings.jsonl').read_text().splitlines()]
            fixed={'actual':{str(r['visitorid']):r['actual'] for r in prior},'seen':{str(r['visitorid']):r['seen'] for r in prior}}
            static=data.static_rows(fixed,cutoff,end);session,population=data.session_rows(cutoff,end)
            for protocol,rows in (('static',static),('session',session)):
                configs=[selection[protocol]['selected_configuration']]
                scores,profiles=evaluate(data,cutoff,rows,configs,pop,{r['visitorid']:r['selected'] for r in prior} if protocol=='static' else None)
                extra={'reference_run_id':reference_run,'reference_rankings_file':'primary_test_rankings.jsonl'} if protocol=='static' else {'population':population,'seen_scope':'same session only; older-session repeats remain eligible'}
                # Catalog diagnostic is computed once, with no removal of cold targets.
                if protocol=='session':
                    catalog=set(map(int,data.a['itemid'][data.a['timestamp']<=cutoff]));extra['cold_item_targets']=sum(r['actual'][0] not in catalog for r in rows)
                result=comparison(root,protocol+'_test',rows,configs[0],cutoff,
                    'static macro-user novel held-out items; same frozen primary cohort' if protocol=='static' else 'session next-new-item conditional query; one target per session; full candidate ranking',extra)
                gate=selection[protocol]['validation_beats_current_all_three'] and strictly_better(result['models']['selected'],result['models']['current_item_item'])
                selection[protocol]['promotion_gate_passed']=gate
                atomic_json(root/f'metrics/{protocol}_promotion.json',{'passed':gate,'rule':plan[protocol+'_promotion'],'checked_at_utc':utc_now()})
                print(protocol.upper()+' TEST '+json.dumps(result['models']),flush=True)
            stage('build_full_history_session_index_and_export')
            serving=root/'serving';serving.mkdir()
            session_config=selection['session']['selected_configuration'] if selection['session']['promotion_gate_passed'] else CURRENT
            graph=SparseGraph(data,end,session_config['graph']);index_profile=graph.export(serving/'session_neighbors.sqlite');del graph;gc.collect()
            session_result=json.loads((root/'metrics/session_test_comparison.json').read_text())
            session_metrics={**session_result['models'],'protocol':session_result['protocol'],'k':10,
                'population':session_result['population'],'cold_item_targets':session_result['cold_item_targets'],
                'paired_visitor_cluster_bootstrap_95':session_result['paired_visitor_cluster_bootstrap_95'],
                'selected_configuration':session_result['selected_configuration'],
                'selected_model_promoted':selection['session']['promotion_gate_passed'],
                'served_policy':session_config['name'],'test_reuse_disclosure':plan['test_reuse_disclosure']}
            model_version=f'{run_id}:session-serving-v1'
            bundle={**old_bundle,'run_id':run_id,'model_version':model_version,'trained_at_utc':utc_now()}
            bundle['session_model']={'model_version':model_version+':session','index_file':'session_neighbors.sqlite',
                                     'metric_model_version':run_id+':session-evaluation-v1','reference_run_id':reference_run,
                                     'configuration':session_config,'metrics':session_metrics,'index_profile':index_profile}
            shutil.copy2(reference/'serving/seen_items.sqlite',serving/'seen_items.sqlite')
            static_result=json.loads((root/'metrics/static_test_comparison.json').read_text())
            if selection['static']['promotion_gate_passed']:
                static_config=selection['static']['selected_configuration'];ids=sorted(int(u) for u in old_bundle['recommendations'])
                graph=SparseGraph(data,end,static_config['graph'])
                with closing(sqlite3.connect(serving/'seen_items.sqlite')) as db:
                    fixed={'actual':{str(u):[] for u in ids},'seen':{str(u):[int(r[0]) for r in db.execute('SELECT itemid FROM seen_items WHERE visitorid=?',(u,))] for u in ids}}
                rows=data.static_rows(fixed,end,end+1);seeds={s for r in rows for s,_ in context_affinities(r['context'],static_config,end)}
                neighbors=graph.neighbors(seeds);full_pop=data.popularity(end)
                bundle['recommendations']={str(r['visitorid']):rank_context(neighbors,r['context'],full_pop,r['seen'],100,static_config,end)['items'] for r in rows}
                del graph
                bundle['metrics']={**old_bundle['metrics'],'heldout_test':static_result['models']['selected'],
                    'metric_model_version':f'{run_id}:weighted-static-evaluation-v1','selected_policy':static_config,
                    'quality_comparison':{'current_item_item':static_result['models']['current_item_item'],'popularity_baseline':static_result['models']['popularity'],'selected_policy':static_result['models']['selected']}}
                bundle['metrics'].update(event_weights=static_config['event_weights'],
                    training_configuration={'source_input_events':2755641,'graph':static_config['graph'],
                                            'max_distinct_items_per_entity':20,'neighbors_per_seed':100,'query_seeds':5},
                    confidence_transform='max intent weight per entity-item with 30-day decay; weighted cosine with cooccurrence shrink3',
                    feature_pipeline={'graph':static_config['graph'],'query_configuration':static_config},
                    selected_parameters=static_config)
                bundle['metrics'].pop('secondary_test',None);bundle['metrics'].pop('paired_primary_bootstrap_95',None)
            else:
                bundle['metrics']={**old_bundle['metrics'],'static_model_reused_from_run_id':reference_run,
                    'static_upgrade_experiment':{'selected':static_result['models']['selected'],'current_item_item':static_result['models']['current_item_item'],
                                                 'promoted':False,'reason':'Did not strictly beat current on all three validation AND test metrics'}}
            bundle['metrics'].update(run_id=run_id,source_run_id=source_run,serving_model_version=model_version,
                                     session_evaluation=session_metrics,session_model_version=model_version+':session',trained_at_utc=bundle['trained_at_utc'])
            atomic_json(root/'metrics/model_metrics.json',bundle['metrics']);atomic_json(root/'metrics/temporal_split.json',split)
            kpis=json.loads((reference/'metrics/kpis.json').read_text());kpis['run_id']=run_id;atomic_json(root/'metrics/kpis.json',kpis)
            from service.store import validate_bundle
            validate_bundle(bundle,run_id);atomic_json(serving/'model_bundle.json',bundle)
            report['stages'].update(training=bundle['metrics'],session_index=index_profile)
            # Run an independent artifact audit BEFORE exposing the new bundle.
            stage('independent_audit_before_activation')
            from scripts.verify_session_upgrade import verify
            audit=verify(root,Path(output_root))
            atomic_json(root/'metrics/independent_session_audit.json',audit)
            report['stages']['independent_audit']=audit
            stage('publish_registry_and_activate')
            from pipeline.runner import publish_model_registry
            report['stages']['model_registry']=publish_model_registry(root,run_id,os.environ['MONGO_URI'],os.environ['REDIS_URL'])
            report.update(status='completed',finished_at_utc=utc_now(),elapsed_seconds=round(time.monotonic()-start,3),
                          static_upgrade_promoted=selection['static']['promotion_gate_passed'],session_upgrade_promoted=selection['session']['promotion_gate_passed'])
            atomic_json(root/'metrics/run_report.json',report)
            atomic_json(Path(output_root)/'latest_run.json',{'run_id':run_id,'model_version':model_version,'activated_at_utc':utc_now()})
            return report
        except Exception as error:
            report.update(status='failed',error=f'{type(error).__name__}: {error}',finished_at_utc=utc_now())
            atomic_json(root/'metrics/run_report.json',report);raise


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-run',required=True);parser.add_argument('--reference-run',required=True);parser.add_argument('--run-id',required=True)
    parser.add_argument('--output-root',default=os.getenv('OUTPUT_ROOT','outputs'))
    args=parser.parse_args();result=execute(args.source_run,args.reference_run,args.run_id,args.output_root)
    print(json.dumps({'run_id':result['run_id'],'status':result['status'],'static_upgrade_promoted':result['static_upgrade_promoted'],
                      'session_upgrade_promoted':result['session_upgrade_promoted']},indent=2))
