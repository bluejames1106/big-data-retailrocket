"""Reproduce one historical session prediction from CSV without changing the served model.

Requires NumPy/SciPy (included in the Docker image). The example is deliberately
selected to explain a hit; it is NOT a representative quality estimate.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import csv
import hashlib
import json
from pathlib import Path
import sqlite3

from pipeline.ranking import user_ranking_metrics
from pipeline.session_model import context_affinities, rank_context, score_context
from pipeline.session_multilabel import remaining_session_labels
from pipeline.session_transition import fit_transitions, strength_fused_rank, fused_rank
from pipeline.session_upgrade import EventData, SparseGraph, canonical_hash
import scripts.experiment_session_coverage as coverage


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--query-id', default='1000484:1441329333729')
    args = parser.parse_args()
    root = args.project_root.resolve()
    pointer = json.loads((root / 'outputs/latest_run.json').read_text(encoding='utf-8-sig'))
    run = root / 'outputs/runs' / pointer['run_id']
    bundle = json.loads((run / 'serving/model_bundle.json').read_text(encoding='utf-8-sig'))
    report = json.loads((run / 'metrics/session_heldout_comparison.json').read_text(encoding='utf-8-sig'))
    config = bundle['session_model']['configuration']
    assert config['fusion'] == 'strength' and config['transition_weight'] == .25
    assert config['strength_power'] == .5 and config['binary_similarity'] == 'conditional'
    cutoff = report['transition_profile']['cutoff']
    raw_file = root / 'data/raw/events.csv'
    source_hash = hashlib.file_digest(raw_file.open('rb'), 'sha256').hexdigest()
    assert source_hash == '3745aa83238b1e6d44d8fda209807899f420084398f94ddf745f3cbcfecbf9e7'

    # Use the same raw-row deduplication and deterministic cohort code as the evaluation.
    coverage.ROOT = root
    raw_count, arrays = coverage.load_clean_csv()
    assert raw_count == 2756101 and len(arrays['timestamp']) == 2755641
    data = EventData(arrays)
    rows, population = data.session_rows(cutoff, int(data.a['timestamp'].max()))
    cohort_hash = canonical_hash(rows)
    assert cohort_hash == report['source_cohort_sha256'], 'Frozen cohort was not reproduced.'
    remaining_session_labels(data, rows, int(data.a['timestamp'].max()))
    row = next(value for value in rows if value['query_id'] == args.query_id)
    assert all(event['timestamp'] <= row['as_of'] < row['target_timestamp'] for event in row['context'])
    assert cutoff < row['target_timestamp']
    seeds = [seed for seed, _ in context_affinities(row['context'], config, row['as_of'])]
    graph = SparseGraph(data, cutoff, 'binary_user', binary_similarity='conditional', shrinkage=3)
    neighbors = graph.neighbors(seeds)
    transitions, _ = fit_transitions(data, cutoff)
    transitions = {seed: transitions.get(seed, []) for seed in seeds}
    popularity = data.popularity(cutoff)
    scored = strength_fused_rank(neighbors, transitions, row['context'], popularity, row['seen'],
                                 .25, .5, 10, config, row['as_of'])
    previous = fused_rank(neighbors, transitions, row['context'], popularity, row['seen'],
                          .25, 10, config, row['as_of'])
    popular = rank_context({}, row['context'], popularity, row['seen'], 10, config, row['as_of'])
    ids = [value['itemid'] for value in scored['items']]
    assert not set(ids) & set(row['seen']) and len(ids) == len(set(ids)) == 10

    details = []
    for rank, recommendation in enumerate(scored['items'], 1):
        item = recommendation['itemid']
        terms = []
        for seed, affinity in context_affinities(row['context'], config, row['as_of']):
            co = dict(neighbors[seed]).get(item, 0.)
            trans = dict(transitions[seed]).get(item, 0.)
            co_max = neighbors[seed][0][1] if neighbors[seed] else 0.
            trans_max = transitions[seed][0][1] if transitions[seed] else 0.
            co_term = .75 * affinity * (co / co_max) ** .5 if co_max and co else 0.
            trans_term = .25 * affinity * (trans / trans_max) ** .5 if trans_max and trans else 0.
            both = int(graph.binary[:, seed].multiply(graph.binary[:, item]).sum())
            terms.append({'seed': seed, 'affinity': affinity,
                          'seed_users_after_cap': int(graph.frequency[seed]),
                          'candidate_users_after_cap': int(graph.frequency[item]),
                          'both_users_after_cap': both, 'co_score': co,
                          'transition_count': trans, 'max_co_score': co_max,
                          'max_transition_count': trans_max,
                          'co_contribution': co_term, 'transition_contribution': trans_term})
        assert abs(sum(t['co_contribution'] + t['transition_contribution'] for t in terms)
                   - recommendation['score']) < 1e-12
        details.append({'rank': rank, **recommendation, 'relevant': item in row['actual'], 'terms': terms})

    source_rows = []
    with raw_file.open(newline='', encoding='utf-8-sig') as source:
        reader = csv.DictReader(source)
        for value in reader:
            if int(value['visitorid']) == row['visitorid']:
                source_rows.append({'source_line': reader.line_num, **value})
    source_rows.sort(key=lambda value: (int(value['timestamp']), int(value['itemid'])))
    source_events = {(int(value['timestamp']), int(value['itemid']), value['event']) for value in source_rows}
    assert all((e['timestamp'], e['itemid'], e['event']) in source_events for e in row['context'])

    # Full-history graph inference is shown separately; never used as a historical test.
    served = score_context(run / 'serving/session_neighbors.sqlite', row['context'],
                           bundle['popular_items'], set(row['seen']), 10, config, row['as_of'])
    with closing(sqlite3.connect((run / 'serving/seen_items.sqlite').resolve().as_uri() + '?mode=ro', uri=True)) as db:
        known_seen = {int(value[0]) for value in db.execute(
            'SELECT itemid FROM seen_items WHERE visitorid=?', (row['visitorid'],))}
    known = score_context(run / 'serving/session_neighbors.sqlite', row['context'],
                          bundle['popular_items'], known_seen, 10, config, row['as_of'])
    result = {
        'status': 'passed', 'selection': 'Previously used successful example, selected for explanation; not representative.',
        'source_csv_sha256': source_hash, 'raw_rows': raw_count, 'clean_rows': len(arrays['timestamp']),
        'source_events': source_rows, 'query': row, 'configuration': config,
        'cohort_sha256': cohort_hash, 'cohort_population': population,
        'historical_graph': graph.profile,
        'visitor_training_events': int(((data.a['visitorid'] == row['visitorid']) &
                                       (data.a['timestamp'] <= cutoff)).sum()),
        'historical_ranking': details,
        'example_metrics': user_ranking_metrics(ids, row['actual'], 10),
        'previous_rrf_ids': [value['itemid'] for value in previous['items']],
        'popularity_ids': [value['itemid'] for value in popular['items']],
        'popularity_example_metrics': user_ranking_metrics(
            [value['itemid'] for value in popular['items']], row['actual'], 10),
        'served_full_history': {'run_id': pointer['run_id'], 'anonymous': served,
                               'known_visitor_seen_ids': sorted(known_seen), 'known_visitor': known,
                               'scope': 'Inference illustration only; graph includes historical targets.'},
        'unchanged_serving': True,
        'checks': {'source_hash': True, 'frozen_cohort_hash': True, 'strictly_past_context': True,
                   'graph_before_cutoff': True, 'no_seen_or_duplicate_ids': True,
                   'score_decomposition_matches': True, 'context_found_in_raw_csv': True},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    csv_file = args.output.with_name('events_user_' + str(row['visitorid']) + '.csv')
    with csv_file.open('w', newline='', encoding='utf-8') as target:
        writer = csv.DictWriter(target, fieldnames=list(source_rows[0]))
        writer.writeheader()
        writer.writerows(source_rows)
    print(json.dumps({'status': 'passed', 'output': str(args.output), 'historical_ids': ids,
                      'actual': row['actual'], 'example_metrics': result['example_metrics'],
                      'visitor_training_events': result['visitor_training_events']}), flush=True)


if __name__ == '__main__':
    main()
