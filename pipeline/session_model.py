"""Shared, dependency-free item-neighborhood inference for offline and HTTP use."""
from __future__ import annotations

from contextlib import closing
import math
from pathlib import Path
import sqlite3
import time

EVENT_WEIGHTS = {'view': 1.0, 'addtocart': 3.0, 'transaction': 5.0}


def context_affinities(events, config=None, as_of=None):
    config = config or {}
    as_of = int(time.time() * 1000) if as_of is None else int(as_of)
    weights = config.get('event_weights', EVENT_WEIGHTS)
    half_life = config.get('query_half_life_minutes', 30)
    decay = config.get('position_decay', .8)
    limit = config.get('seed_limit', 5)
    values = {}
    for row in events:
        item, stamp = int(row['itemid']), int(row['timestamp'])
        if stamp > as_of:
            raise ValueError('Future events cannot be used as context')
        strength = float(weights[row['event']])
        if half_life:
            strength *= math.exp2(-(as_of - stamp) / (float(half_life) * 60000))
        latest, best = values.get(item, (-1, 0.0))
        values[item] = max(latest, stamp), max(best, strength)
    ordered = sorted(values, key=lambda i: (-values[i][0], i))[:limit]
    return [(item, values[item][1] * decay ** rank) for rank, item in enumerate(ordered)]


def rank_context(neighbors, events, popular, seen, k=10, config=None, as_of=None):
    """Identical candidate universe and exclusion rules for every compared policy."""
    config = config or {}
    seen = set(seen) | {int(e['itemid']) for e in events}
    affinities = context_affinities(events, config, as_of)
    scores, matched = {}, 0
    for seed, affinity in affinities:
        edges = neighbors.get(seed, ())
        if edges:
            matched += 1
        for item, similarity in edges:
            if item not in seen:
                scores[item] = scores.get(item, 0.0) + affinity * similarity
    order = [item for item, score in sorted(scores.items(), key=lambda p: (-p[1], p[0]))[:100] if score > 0]
    pop = list(dict.fromkeys(int(p['itemid'] if isinstance(p, dict) else p) for p in popular))
    pop = [i for i in pop if i not in seen]
    fraction = float(config.get('popularity_weight', 0)) if order else 1.0
    combined = {}
    for ids, weight in ((order, 1-fraction), (pop, fraction)):
        if weight:
            for rank, item in enumerate(ids, 1):
                combined[item] = combined.get(item, 0.0) + weight / (60+rank)
    result = [{'itemid': i, 'score': s} for i, s in sorted(combined.items(), key=lambda p: (-p[1], p[0]))[:k]]
    emitted = {r['itemid'] for r in result}
    for item in pop:
        if len(result) >= k:
            break
        if item not in emitted:
            result.append({'itemid': item, 'score': 0.0})
            emitted.add(item)
    return {'items': result, 'context_items': len({e['itemid'] for e in events}),
            'matched_seeds': matched, 'source': 'session_item_item' if order else 'popular_fallback'}


def score_context(index_path, events, popular, seen, k=10, config=None, as_of=None):
    path = Path(index_path)
    if not path.is_file() or path.is_symlink():
        raise FileNotFoundError('Session neighborhood index is unavailable')
    seeds = context_affinities(events, config, as_of)
    neighbors = {}
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=1)) as db:
        for seed, _ in seeds:
            neighbors[seed] = [(int(r[0]), float(r[1])) for r in db.execute(
                'SELECT candidate, score FROM neighbors WHERE seed=? ORDER BY score DESC,candidate', (seed,))]
    transition_weight = float((config or {}).get('transition_weight', 0))
    if transition_weight:
        from pipeline.session_transition import fused_rank, read_transitions, strength_fused_rank
        transitions = read_transitions(path, [seed for seed, _ in seeds])
        if (config or {}).get('fusion') == 'strength':
            return strength_fused_rank(neighbors, transitions, events, popular, seen,
                                       transition_weight, float(config.get('strength_power', 1)),
                                       k, config, as_of)
        return fused_rank(neighbors, transitions, events, popular, seen, transition_weight, k, config, as_of)
    return rank_context(neighbors, events, popular, seen, k, config, as_of)
