"""Chronological next-new-item transitions and rank fusion for session serving."""
from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import closing
from pathlib import Path
import sqlite3

from pipeline.session_model import context_affinities


def fit_transitions(data, cutoff, limit=100):
    """Use only training events; same-timestamp items never predict one another."""
    timestamp, item = data.a['timestamp'], data.a['itemid']
    pairs = Counter()
    active = 0
    for start, stop in zip(data.starts, data.ends):
        if timestamp[start] > cutoff:
            continue
        seen = set()
        previous = set()
        at = int(start)
        while at < stop and timestamp[at] <= cutoff:
            until = at + 1
            while until < stop and timestamp[until] == timestamp[at]:
                until += 1
            group = {int(i) for i in item[at:until]}
            novel = group - seen
            if previous and novel:
                for seed in previous:
                    for candidate in novel:
                        if seed != candidate:
                            pairs[seed, candidate] += 1
                            active += 1
            seen.update(group)
            previous = group
            at = until
    by_seed = defaultdict(list)
    for (seed, candidate), count in pairs.items():
        by_seed[seed].append((candidate, count))
    return {seed: sorted(edges, key=lambda row: (-row[1], row[0]))[:limit]
            for seed, edges in by_seed.items()}, {'pairs': len(pairs), 'observed_transitions': active,
                                                   'seeds': len(by_seed), 'limit': limit, 'cutoff': int(cutoff)}


def fit_continuations(data, cutoff, limit=100, max_distinct_items=20):
    """Link each new item to later new items in the same training session."""
    timestamp, item = data.a['timestamp'], data.a['itemid']
    pairs = Counter()
    contributions = 0
    for start, stop in zip(data.starts, data.ends):
        if timestamp[start] > cutoff:
            continue
        first_seen = {}
        for index in range(int(start), int(stop)):
            if timestamp[index] > cutoff:
                break
            value = int(item[index])
            if value not in first_seen and len(first_seen) < max_distinct_items:
                first_seen[value] = int(timestamp[index])
        ordered = list(first_seen.items())
        for earlier, (seed, seed_time) in enumerate(ordered):
            for candidate, candidate_time in ordered[earlier + 1:]:
                if seed_time < candidate_time:
                    pairs[seed, candidate] += 1
                    contributions += 1
    by_seed = defaultdict(list)
    for (seed, candidate), count in pairs.items():
        by_seed[seed].append((candidate, count))
    return {seed: sorted(edges, key=lambda row: (-row[1], row[0]))[:limit]
            for seed, edges in by_seed.items()}, {'pairs': len(pairs), 'observed_continuations': contributions,
                                                   'seeds': len(by_seed), 'limit': limit,
                                                   'max_distinct_items': max_distinct_items, 'cutoff': int(cutoff)}


def fused_rank(co_neighbors, transitions, events, popular, seen, weight, k=10, config=None, as_of=None):
    """Fuse ranked lists; only actual graph edges receive a fusion score."""
    if not 0 <= weight <= 1:
        raise ValueError('transition weight must be in [0,1]')
    config = config or {}
    excluded = set(seen) | {int(e['itemid']) for e in events}
    affinities = context_affinities(events, config, as_of)
    lists = []
    matched = 0
    for graph in (co_neighbors, transitions):
        scores = {}
        local_matched = 0
        for seed, affinity in affinities:
            edges = graph.get(seed, ())
            local_matched += bool(edges)
            for candidate, strength in edges:
                if candidate not in excluded:
                    scores[candidate] = scores.get(candidate, 0.0) + affinity * strength
        matched += local_matched
        lists.append([i for i, s in sorted(scores.items(), key=lambda row: (-row[1], row[0]))[:100] if s > 0])
    scores = {}
    for listing, factor in zip(lists, (1-weight, weight)):
        if factor:
            for rank, candidate in enumerate(listing, 1):
                scores[candidate] = scores.get(candidate, 0.0) + factor / (60 + rank)
    ranked = sorted(scores.items(), key=lambda row: (-row[1], row[0]))[:k]
    result = [{'itemid': item, 'score': score} for item, score in ranked]
    used = {row['itemid'] for row in result}
    for row in popular:
        if len(result) >= k:
            break
        item = int(row['itemid'] if isinstance(row, dict) else row)
        if item not in excluded and item not in used:
            result.append({'itemid': item, 'score': 0.0})
            used.add(item)
    return {'items': result, 'context_items': len({e['itemid'] for e in events}),
            'matched_seeds': matched, 'source': 'session_item_item_transition' if scores else 'popular_fallback'}


def continuation_rank(co_neighbors, transitions, continuations, events, popular, seen,
                      continuation_weight, k=10, config=None, as_of=None):
    """Fuse current item-item/transition policy with later-session associations."""
    if not 0 <= continuation_weight <= 1:
        raise ValueError('continuation weight must be in [0,1]')
    config = config or {}
    excluded = set(seen) | {int(e['itemid']) for e in events}
    affinities = context_affinities(events, config, as_of)
    weights = ((1-continuation_weight) * .75, (1-continuation_weight) * .25, continuation_weight)
    scores = {}
    matched = 0
    for graph, factor in zip((co_neighbors, transitions, continuations), weights):
        values = {}
        for seed, affinity in affinities:
            edges = graph.get(seed, ())
            matched += bool(edges)
            for candidate, strength in edges:
                if candidate not in excluded:
                    values[candidate] = values.get(candidate, 0.0) + affinity * strength
        if factor:
            order = [item for item, score in sorted(values.items(), key=lambda row: (-row[1], row[0]))[:100]
                     if score > 0]
            for rank, candidate in enumerate(order, 1):
                scores[candidate] = scores.get(candidate, 0.0) + factor / (60 + rank)
    ranked = sorted(scores.items(), key=lambda row: (-row[1], row[0]))[:k]
    result = [{'itemid': item, 'score': score} for item, score in ranked]
    used = {row['itemid'] for row in result}
    for row in popular:
        if len(result) >= k:
            break
        item = int(row['itemid'] if isinstance(row, dict) else row)
        if item not in excluded and item not in used:
            result.append({'itemid': item, 'score': 0.0})
            used.add(item)
    return {'items': result, 'context_items': len({e['itemid'] for e in events}),
            'matched_seeds': matched, 'source': 'session_item_item_transition_continuation' if scores else 'popular_fallback'}


def popularity_blend_rank(co_neighbors, transitions, events, popular, seen,
                          popularity_weight, k=10, config=None, as_of=None):
    """Blend the served graph ranking with recent popularity after seen filtering."""
    if not 0 <= popularity_weight <= 1:
        raise ValueError('popularity weight must be in [0,1]')
    base = fused_rank(co_neighbors, transitions, events, popular, seen, .25, 100, config, as_of)
    excluded = set(seen) | {int(e['itemid']) for e in events}
    scores = {row['itemid']: (1-popularity_weight)*row['score'] for row in base['items'] if row['score'] > 0}
    if popularity_weight:
        pop = list(dict.fromkeys(int(row['itemid'] if isinstance(row, dict) else row) for row in popular))
        for rank, item in enumerate(pop[:100], 1):
            if item not in excluded:
                scores[item] = scores.get(item, 0.0) + popularity_weight/(60+rank)
    result = [{'itemid': item, 'score': score} for item, score in
              sorted(scores.items(), key=lambda row: (-row[1], row[0]))[:k]]
    used = {row['itemid'] for row in result}
    for row in popular:
        if len(result) >= k:
            break
        item = int(row['itemid'] if isinstance(row, dict) else row)
        if item not in excluded and item not in used:
            result.append({'itemid': item, 'score': 0.0})
            used.add(item)
    return {'items': result, 'context_items': base['context_items'],
            'matched_seeds': base['matched_seeds'],
            'source': 'session_item_item_transition_popularity' if scores else 'popular_fallback'}


def strength_fused_rank(co_neighbors, transitions, events, popular, seen,
                        transition_weight, power=1, k=10, config=None, as_of=None):
    """Combine per-seed normalized edge strengths instead of reciprocal ranks."""
    if not 0 <= transition_weight <= 1 or power <= 0:
        raise ValueError('Invalid score fusion parameters')
    config = config or {}
    excluded = set(seen) | {int(e['itemid']) for e in events}
    affinities = context_affinities(events, config, as_of)
    scores = {}
    matched = 0
    for graph, factor in ((co_neighbors, 1-transition_weight), (transitions, transition_weight)):
        if not factor:
            continue
        for seed, affinity in affinities:
            edges = graph.get(seed, ())
            if not edges:
                continue
            matched += 1
            maximum = float(edges[0][1])
            if maximum <= 0:
                continue
            for candidate, strength in edges:
                if candidate not in excluded and strength > 0:
                    normalized = float(strength) / maximum
                    scores[candidate] = scores.get(candidate, 0.0) + factor * affinity * normalized ** power
    result = [{'itemid': item, 'score': score} for item, score in
              sorted(scores.items(), key=lambda row: (-row[1], row[0]))[:k]]
    used = {row['itemid'] for row in result}
    for row in popular:
        if len(result) >= k:
            break
        item = int(row['itemid'] if isinstance(row, dict) else row)
        if item not in excluded and item not in used:
            result.append({'itemid': item, 'score': 0.0})
            used.add(item)
    return {'items': result, 'context_items': len({e['itemid'] for e in events}),
            'matched_seeds': matched,
            'source': 'session_strength_fusion' if scores else 'popular_fallback'}


def read_transitions(index_path, seeds):
    path = Path(index_path)
    if not path.is_file() or path.is_symlink():
        raise FileNotFoundError('Session transition index is unavailable')
    result = {}
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=1)) as db:
        for seed in seeds:
            result[seed] = [(int(candidate), float(score)) for candidate, score in db.execute(
                'SELECT candidate, score FROM transitions WHERE seed=? ORDER BY score DESC,candidate', (seed,))]
    return result


def read_continuations(index_path, seeds):
    path = Path(index_path)
    if not path.is_file() or path.is_symlink():
        raise FileNotFoundError('Session continuation index is unavailable')
    result = {}
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=1)) as db:
        for seed in seeds:
            result[seed] = [(int(candidate), float(score)) for candidate, score in db.execute(
                'SELECT candidate, score FROM continuations WHERE seed=? ORDER BY score DESC,candidate', (seed,))]
    return result
