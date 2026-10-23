"""Exercise real HTTP endpoints and measure a small reproducible session workload."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import statistics
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from pipeline.config import atomic_json, run_path, utc_now


def verify(base_url, run_id, output_root='outputs'):
    base_url=base_url.rstrip('/')
    def request(path,payload=None):
        data=None if payload is None else json.dumps(payload).encode()
        req=Request(base_url+path,data=data,headers={'Content-Type':'application/json'})
        with urlopen(req,timeout=30) as response:return json.load(response)
    health=request('/health/ready');metrics=request('/api/metrics')
    assert metrics['run_id']==run_id and health['model']['run_id']==run_id
    assert metrics['session_model']['available'] and not health['model']['stale']
    version=metrics['model_version'];session_version=metrics['session_model']['model_version']
    assert metrics['session_model']['metric_model_version']!=session_version
    def session(events,visitor=None):
        payload={'events':events,'k':10}
        if visitor is not None:payload['visitorid']=visitor
        reply=request('/api/session/recommendations',payload)
        items=[r['itemid'] for r in reply['items']]
        assert reply['run_id']==run_id and reply['model_version']==version and reply['session_model_version']==session_version
        assert len(items)==10 and len(items)==len(set(items)) and not set(items)&{e['itemid'] for e in events}
        return reply
    initial=session([]);first=session([{'itemid':434782,'event':'view'}])
    second=session([{'itemid':434782,'event':'view'},{'itemid':215363,'event':'addtocart'}])
    isolated=session([{'itemid':17114,'event':'transaction'}]);again=session([])
    assert initial['source']=='popular_fallback' and initial['items']==again['items']
    assert all(r['source'] in {'session_item_item','session_item_item_transition','session_strength_fusion'} for r in (first,second))
    assert first['items']!=initial['items'] and second['items']!=first['items'] and isolated['items']!=second['items']
    users=request('/api/users?limit=1');user=users['user_ids'][0]
    known=session([{'itemid':434782,'event':'view'}],user)
    import sqlite3
    from contextlib import closing
    with closing(sqlite3.connect((run_path(run_id,output_root)/'serving/seen_items.sqlite').resolve().as_uri()+'?mode=ro',uri=True)) as db:
        seen={r[0] for r in db.execute('SELECT itemid FROM seen_items WHERE visitorid=?',(user,))}
    assert not seen&{r['itemid'] for r in known['items']}
    future_rejected=False
    try:session([{'itemid':1,'event':'view','timestamp':int(time.time()*1000)+60000}])
    except HTTPError as error:future_rejected=error.code==422
    assert future_rejected
    def measured(index):
        started=time.perf_counter();session([{'itemid':434782 if index%2==0 else 17114,'event':'view'}])
        return (time.perf_counter()-started)*1000
    for i in range(3):measured(i)
    started=time.perf_counter()
    with ThreadPoolExecutor(max_workers=4) as pool:latencies=list(pool.map(measured,range(100)))
    ordered=sorted(latencies);elapsed=time.perf_counter()-started
    result={'status':'passed','run_id':run_id,'model_version':version,'session_model_version':session_version,
            'metric_model_version':metrics['session_model']['metric_model_version'],'measured_at_utc':utc_now(),
            'checks':{'ready_and_version':True,'empty_context_fallback':True,'click_changes_ranking':True,
                      'second_context_changes_ranking':True,'sessions_isolated':True,'full_seen_filtered':True,'future_event_rejected':True},
            'session_benchmark':{'requests':100,'successful':100,'concurrency':4,'warmups':3,'elapsed_seconds':elapsed,
                                  'latency_ms':{'mean':statistics.mean(latencies),'p50':statistics.median(latencies),'p95':ordered[94],'p99':ordered[98]},
                                  'scope':'HTTP session endpoint with two fixed seeds on local demo; not a production SLA'},
            'examples':{'empty':initial,'view':first,'view_then_cart':second,'known_user':known}}
    atomic_json(run_path(run_id,output_root)/'metrics/session_demo_verification.json',result)
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--base-url',default='http://api:8000')
    p.add_argument('--run-id',required=True);p.add_argument('--output-root',default='outputs');args=p.parse_args()
    result=verify(args.base_url,args.run_id,args.output_root)
    print(json.dumps({k:v for k,v in result.items() if k!='examples'},indent=2))
