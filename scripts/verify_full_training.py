"""Verify the real full-history model and serving; no full Mongo facts claim."""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import urllib.request
from datetime import datetime, timezone

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--run-id',required=True)
run=parser.parse_args().run_id
root=Path('/app/outputs/runs')/run
def http(route):
    with urllib.request.urlopen('http://api:8000'+route,timeout=60) as response:
        return json.load(response)

report=json.loads((root/'metrics/run_report.json').read_text())
model=json.loads((root/'metrics/model_metrics.json').read_text())
assert report['status']=='completed'
assert report['stages']['history_import']['raw_rows']==2756101
assert model['input_events']==2755641
assert report['stages']['lake']['silver_events']==2755641
assert model['serving_scope']['trained_users']==1407580
assert model['serving_scope']['training_catalog_size']==235061
assert model['heldout_test']['eligible_users']==1000
assert model['heldout_test']['population_eligible_users']>=1000
assert report['stages']['acid']['status']=='passed'
if model.get('selected_policy'):
    selection=json.loads((root/'metrics/policy_selection.json').read_text())
    assert selection['selected']['configuration']==model['selected_policy']
    order=(lambda row:(row['minimum_validation_ratio'],row['mean_ndcg_at_k'],row['mean_recall_at_k'],row['mean_precision_at_k'])) if selection.get('selection_rule')=='minimum_validation_ratio_then_ndcg_recall_precision' else (lambda row:(row['mean_ndcg_at_k'],row['mean_recall_at_k'],row['mean_precision_at_k']))
    assert max(selection['all_candidates'],key=order)['configuration']==model['selected_policy']
    comparison=model['quality_comparison']
    old=comparison['previous_reported_als']
    for key in ('eligible_users','population_eligible_users','novel_heldout_pairs','heldout_unique_pairs','excluded_repeat_pairs','cold_users','cold_item_pairs','training_catalog_size'):
        assert model['heldout_test'][key]==old[key],key
    for key in ('precision_at_k','recall_at_k','ndcg_at_k'):
        assert abs(comparison['als_recomputed_same_cohort'][key]-old[key])<1e-10,key
    assert datetime.fromisoformat(selection['selected_at_utc'])<datetime.fromisoformat(model['evaluated_at_utc'])
ready=http('/health/ready')
assert ready['status']=='ready',ready
metrics=http('/api/metrics')
assert metrics['run_id']==run
assert metrics['kpis']['silver_events']==2755641
assert metrics['model_metrics']['heldout_test']==model['heldout_test']
assert metrics['model_version']==model['serving_model_version']
known=http('/api/users?limit=3')['user_ids']
bundle=json.loads((root/'serving/model_bundle.json').read_text())
tested=[]
with sqlite3.connect(root/'serving/seen_items.sqlite') as con:
    total=con.execute('SELECT COUNT(*) FROM seen_items').fetchone()[0]
    assert total==2145179,total
    outside=next(int(row[0]) for row in con.execute('SELECT DISTINCT visitorid FROM seen_items ORDER BY visitorid')
                 if str(row[0]) not in bundle['recommendations'])
    for user in known+[outside,2147483647]:
        value=http(f'/api/recommendations/{user}?k=10')
        items=[row['itemid'] for row in value['items']]
        seen={row[0] for row in con.execute('SELECT itemid FROM seen_items WHERE visitorid=?',(user,))}
        assert len(items)==len(set(items)) and not set(items)&seen
        assert value['model_version']==model['serving_model_version']
        tested.append({'visitorid':user,'source':value['source'],'items':items,'seen_count':len(seen)})
from pymongo import MongoClient
import redis
with MongoClient(os.environ['MONGO_URI']) as mongo:
    assert mongo.bigdata.model_registry.count_documents({'run_id':run,'model_version':model['serving_model_version']})==1
cache=redis.Redis.from_url(os.environ['REDIS_URL'],decode_responses=True)
try:
    registry=json.loads(cache.get(f'bigdata:{run}:active_model'))
    assert registry['model_version']==model['serving_model_version']
finally:
    cache.close()
result={'status':'passed','run_id':run,'checked_at_utc':datetime.now(timezone.utc).isoformat(),
        'raw_rows':2756101,'clean_rows':2755641,'trained_users':1407580,'catalog_items':235061,
        'sqlite_seen_pairs':total,'heldout_test':model['heldout_test'],'tested_users':tested,
        'checks':{'full_source_used':True,'all_clean_events_trained_in_serving':True if model['algorithm']!='item_knn_hybrid' else None,
                  'model_api_metrics_match':True,'no_seen_items_returned':True,'mongo_registry':True,
                  'redis_registry':True,'delta_acid':True},
        'scope':'offline full-history model; registry-only Mongo/Redis; complete SQLite seen; fixed 1000-user evaluation cohort'}
result['model_training_scope']=model.get('training_note',model.get('serving_refit_scope'))
result['delta_acid_reused_from_run']=report['stages']['acid'].get('reused_from_run_id')
(root/'metrics/full_model_verification.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result,indent=2))
