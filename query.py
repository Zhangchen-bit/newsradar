"""Read-side helpers for the deduped news feed.

The canonical view ('feed') returns ONE row per cluster: the longest content
(richer source) wins, importance is max across the cluster, and we expose
which sources covered it.
"""
from __future__ import annotations

import sqlite3
import time
import datetime as dt

from db import connect


FEED_SQL = """
WITH cluster_agg AS (
    SELECT
        cluster_id,
        MAX(importance)            AS importance,
        MAX(ts)                    AS latest_ts,
        MIN(ts)                    AS earliest_ts,
        COUNT(*)                   AS source_count,
        GROUP_CONCAT(DISTINCT source) AS sources
    FROM news
    WHERE cluster_id IS NOT NULL
      AND ts >= ?
    GROUP BY cluster_id
),
representative AS (
    -- pick the longest-content row per cluster as the display row
    SELECT n.cluster_id, n.id, n.source, n.title, n.content, n.url, n.ts
    FROM news n
    JOIN (
        SELECT cluster_id, MAX(LENGTH(content)) AS maxlen
        FROM news
        WHERE cluster_id IS NOT NULL AND ts >= ?
        GROUP BY cluster_id
    ) m ON m.cluster_id = n.cluster_id AND LENGTH(n.content) = m.maxlen
    GROUP BY n.cluster_id   -- if ties, sqlite picks one deterministically
)
SELECT
    a.cluster_id, a.importance, a.latest_ts, a.earliest_ts,
    a.source_count, a.sources,
    r.title, r.content, r.url, r.source AS rep_source,
    v.status, v.company, v.evidence_date
FROM cluster_agg a
JOIN representative r ON r.cluster_id = a.cluster_id
LEFT JOIN verifications v ON v.cluster_id = a.cluster_id
WHERE a.importance >= ?
ORDER BY a.latest_ts DESC
LIMIT ?
"""


def feed(
    since_ts: int | None = None,
    min_importance: int = 1,
    limit: int = 50,
) -> list[dict]:
    if since_ts is None:
        since_ts = int(time.time()) - 24 * 3600
    conn = connect()
    rows = conn.execute(
        FEED_SQL, (since_ts, since_ts, min_importance, limit)
    ).fetchall()
    cols = [
        "cluster_id", "importance", "latest_ts", "earliest_ts",
        "source_count", "sources", "title", "content", "url", "rep_source",
        "verify_status", "verify_company", "verify_date",
    ]
    return [dict(zip(cols, r)) for r in rows]


_VERIFY_BADGE = {
    "confirmed": "✓核实",
    "partial":   "△媒体",
    "unconfirmed": "?未证",
    "skip":      "",
    "error":     "!错误",
    None:        "",
}


def print_feed(items: list[dict]):
    for it in items:
        t = dt.datetime.fromtimestamp(it["latest_ts"]).strftime("%H:%M")
        mark = "🔴" if it["importance"] >= 3 else ("🟡" if it["importance"] >= 2 else "  ")
        multi = f"x{it['source_count']}" if it["source_count"] > 1 else "  "
        srcs = it["sources"]
        badge = _VERIFY_BADGE.get(it.get("verify_status"), "")
        badge = f" [{badge}]" if badge else ""
        print(f"{t} {mark} [{srcs:20s}] {multi}{badge}  {it['content'][:100]}")


def latest_summary(window: str) -> dict | None:
    """Return the latest cached summary for a window, or None."""
    import json as _json
    conn = connect()
    row = conn.execute(
        "SELECT id, generated_at, cluster_count, model, topics_json, elapsed_ms "
        "FROM summaries WHERE window=? ORDER BY generated_at DESC LIMIT 1",
        (window,),
    ).fetchone()
    if not row:
        return None
    return {
        "id": row[0], "window": window, "generated_at": row[1],
        "cluster_count": row[2], "model": row[3],
        "topics": _json.loads(row[4]), "elapsed_ms": row[5],
    }


def print_summary(window: str):
    s = latest_summary(window)
    if not s:
        print(f"(no summary yet for window={window})")
        return
    ts = dt.datetime.fromtimestamp(s["generated_at"]).strftime("%Y-%m-%d %H:%M")
    print(f"=== {window} window summary  (generated {ts}, {s['cluster_count']} clusters, "
          f"{s['model']}) ===")
    for topic, points in s["topics"].items():
        if not points:
            continue
        print(f"\n## {topic}")
        for p in points:
            cids = p.get("cluster_ids") or []
            cid_s = f"  [c#{','.join(map(str, cids))}]" if cids else ""
            print(f"  • {p.get('point', '')}{cid_s}")
            ev = p.get("evidence")
            if ev:
                print(f"    └ {ev[:140]}")


if __name__ == "__main__":
    import sys
    args = sys.argv[1:]
    if args and args[0] in ("1h", "5h", "24h"):
        print_summary(args[0])
    else:
        min_imp = int(args[0]) if args else 1
        items = feed(min_importance=min_imp, limit=30)
        print(f"=== deduped feed (last 24h, min_importance={min_imp}, "
              f"{len(items)} clusters) ===")
        print_feed(items)
