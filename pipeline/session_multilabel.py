"""Label all later new items in each frozen session query."""
from __future__ import annotations

from collections import Counter


def remaining_session_labels(data, rows, evaluation_end):
    """Keep the existing query/context cohort and replace one target with all future items.

    Events at or before ``as_of`` remain context. The future stops at the
    session boundary and the evaluation-period end; duplicate items count once.
    """
    by_id = {row['query_id']: row for row in rows}
    if len(by_id) != len(rows):
        raise ValueError('Duplicate session query IDs')
    found = set()
    timestamp, users, items = data.a['timestamp'], data.a['visitorid'], data.a['itemid']
    distribution = Counter()
    for start, stop in zip(data.starts, data.ends):
        key = f'{int(users[start])}:{int(timestamp[start])}'
        row = by_id.get(key)
        if row is None:
            continue
        found.add(key)
        seen = set(row['seen'])
        future = {int(items[index]) for index in range(int(start), int(stop))
                  if row['as_of'] < timestamp[index] <= evaluation_end and int(items[index]) not in seen}
        if not future or not set(row['actual']).issubset(future):
            raise ValueError(f'Frozen next-item target missing from future labels: {key}')
        row['actual'] = sorted(future)
        distribution[len(future)] += 1
    if found != set(by_id):
        raise ValueError('Some frozen session queries were not found in event data')
    return {'queries': len(rows), 'total_future_distinct_items': sum(n * c for n, c in distribution.items()),
            'labels_per_query': dict(sorted(distribution.items())),
            'queries_with_multiple_labels': sum(c for n, c in distribution.items() if n > 1),
            'maximum_labels': max(distribution, default=0)}
