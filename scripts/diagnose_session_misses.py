"""Describe frozen session test errors without selecting a new model on test."""
from __future__ import annotations

from collections import Counter, defaultdict
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
METRICS = ROOT / "outputs/runs/session-20261022-1327/metrics"


def main() -> None:
    rows = [json.loads(line) for line in (METRICS / "session_test_rankings.jsonl").open(encoding="utf-8")]
    totals = Counter()
    by_event = defaultdict(Counter)
    by_context = defaultdict(Counter)
    by_age = defaultdict(Counter)
    for row in rows:
        target = row["actual"][0]
        current = row["current_item_item"]
        candidate = row["selected"]
        popular = row["popularity"]
        hit = target in current
        totals["queries"] += 1
        totals["current_hits"] += hit
        totals["weighted_hits"] += target in candidate
        totals["popular_hits"] += target in popular
        totals["weighted_only_hits"] += target in candidate and not hit
        totals["current_only_hits"] += hit and target not in candidate
        totals["missed_by_both"] += not hit and target not in candidate
        if hit:
            totals[f"current_rank_{current.index(target) + 1}"] += 1
        context = row["context"]
        latest = max(context, key=lambda e: e["timestamp"])
        event = latest["event"]
        nitems = len({e["itemid"] for e in context})
        context_bucket = "1" if nitems == 1 else "2" if nitems == 2 else "3-5" if nitems <= 5 else "6+"
        age_min = (row["as_of"] - latest["timestamp"]) / 60000
        age_bucket = "<1m" if age_min < 1 else "1-5m" if age_min < 5 else "5-15m" if age_min < 15 else "15m+"
        for group, key in ((by_event, event), (by_context, context_bucket), (by_age, age_bucket)):
            group[key]["queries"] += 1
            group[key]["hits"] += hit
    result = {
        "scope": "Descriptive analysis of the previously viewed frozen session test; not used for model selection.",
        "counts": dict(totals),
        "segments": {name: {key: {**dict(c), "recall_at_10": c["hits"] / c["queries"]}
                            for key, c in sorted(groups.items())}
                     for name, groups in (("latest_event", by_event), ("distinct_context_items", by_context), ("latest_event_age", by_age))},
        "limits": ["Only top-10 predictions were saved; this cannot distinguish a target at rank 11-100 from a retrieval miss.",
                   "The serving SQLite graph was refit on all history and must not be used to diagnose held-out candidate recall."],
    }
    out = ROOT / "outputs/evidence/session_miss_diagnosis.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
