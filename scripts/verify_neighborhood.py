"""Recompute point metrics from per-user rankings and verify the frozen study."""
import argparse
import hashlib
import json
from pathlib import Path
from datetime import datetime

from pipeline.ranking import user_ranking_metrics


def verify(root):
    selection=json.loads((root/'metrics/policy_selection.json').read_text())
    report=json.loads((root/'metrics/run_report.json').read_text())
    plan=json.loads((root/'metrics/experiment_plan.json').read_text())
    assert selection['selection_rule']=='minimum_validation_ratio_then_ndcg_recall_precision'
    assert plan['baseline_window_days']==7 and plan['primary']['hash_seed']==42
    assert plan['secondary']['hash_seed']==43 and plan['secondary']['cap']==5000
    names=('precision_at_k','recall_at_k','ndcg_at_k')
    baseline=selection['popularity_baseline']
    for candidate in selection['all_candidates']:
        ratios=[]
        for key in names:
            expected=sum(f[key] for f in candidate['folds'])/len(candidate['folds'])
            assert abs(expected-candidate['mean_'+key])<1e-12
            ratio=expected/baseline['mean_'+key]
            assert abs(ratio-candidate['ratios_to_baseline'][key])<1e-12
            ratios.append(ratio)
        assert abs(min(ratios)-candidate['minimum_validation_ratio'])<1e-12
    order=lambda c:(c['minimum_validation_ratio'],c['mean_ndcg_at_k'],c['mean_recall_at_k'],c['mean_precision_at_k'])
    assert max(selection['all_candidates'],key=order)==selection['selected']
    if not (root/'metrics/primary_test_rankings.jsonl').exists():
        assert selection['selected']['minimum_validation_ratio']<=1
        return {'status':'passed','quality_target_met':False,'note':'Validation gate stopped experiment before test.'}
    cohorts={}
    results={}
    for label,count in [('primary_test',1000),('secondary_test',5000)]:
        rows=[json.loads(s) for s in (root/f'metrics/{label}_rankings.jsonl').read_text().splitlines()]
        recorded=json.loads((root/f'metrics/{label}_comparison.json').read_text())
        assert len(rows)==count and len({row['visitorid'] for row in rows})==count
        assert datetime.fromisoformat(selection['selected_at_utc'])<datetime.fromisoformat(recorded['evaluated_at_utc'])
        canonical=[]
        for row in sorted(rows,key=lambda r:r['visitorid']):
            actual,seen=set(row['actual']),set(row['seen'])
            assert actual and not actual&seen
            canonical.append((row['visitorid'],sorted(actual),sorted(seen)))
            for name in ['selected','baseline']:
                assert len(row[name])<=10 and len(row[name])==len(set(row[name]))
                assert not set(row[name])&seen
        signature=hashlib.sha256(json.dumps(canonical,separators=(',',':')).encode()).hexdigest()
        assert signature==recorded['cohort_sha256']
        for name,recorded_name in [('selected','selected'),('baseline','popularity_baseline')]:
            values=[user_ranking_metrics(row[name],row['actual'],10) for row in rows]
            for key in names:
                value=sum(v[key] for v in values)/len(values)
                assert abs(value-recorded[recorded_name][key])<1e-12,(label,name,key)
            hits=sum(len(set(row[name])&set(row['actual'])) for row in rows)
            assert hits==recorded[recorded_name]['total_hits']
            assert sum(not row['seen'] for row in rows)==recorded[recorded_name]['cold_users']
        expected=all(recorded['selected'][key]>recorded['popularity_baseline'][key] for key in names)
        assert expected==recorded['beats_baseline_all_three']
        cohorts[label]={row['visitorid'] for row in rows}
        results[label]={'cohort_users':count,'cohort_sha256':signature,'recomputed_metrics_match':True,'beats_baseline_all_three':expected}
    assert not cohorts['primary_test']&cohorts['secondary_test']
    gate=json.loads((root/'metrics/quality_gate.json').read_text())
    expected=selection['selected']['minimum_validation_ratio']>1 and results['primary_test']['beats_baseline_all_three']
    assert expected==gate['passed']==report['promotion_gate_passed']
    if report['status']=='completed':assert expected
    return {'status':'passed','quality_target_met':expected,'run_id':report['run_id'],'cohorts_disjoint':True,
            'test_loaded_after_frozen_validation_selection':True,'per_user_metrics_recomputed':True,'results':results}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id',required=True)
    parser.add_argument('--require-promotion',action='store_true')
    args=parser.parse_args()
    root=Path('outputs/runs')/args.run_id
    result=verify(root)
    (root/'metrics/independent_ranking_audit.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2))
    if args.require_promotion and not result['quality_target_met']:raise SystemExit(2)
