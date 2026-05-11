"""Backfill simhash + cluster_id for rows that pre-date the dedup feature.

Processes rows in ts ASC order so cluster ids are stable and earlier items
seed clusters for later same-event arrivals.
"""
from __future__ import annotations

import sys
from db import connect
from dedup import assign_cluster


def backfill(reset: bool = False) -> tuple[int, int]:
    conn = connect()
    if reset:
        conn.execute("UPDATE news SET simhash=NULL, cluster_id=NULL")
        conn.commit()

    rows = conn.execute(
        "SELECT id, ts, content, importance FROM news "
        "WHERE simhash IS NULL OR cluster_id IS NULL "
        "ORDER BY ts ASC"
    ).fetchall()

    processed = 0
    clusters_seen: set[int] = set()
    for row_id, ts, content, importance in rows:
        sh_hex, cid = assign_cluster(conn, int(ts), content, importance=importance)
        conn.execute(
            "UPDATE news SET simhash=?, cluster_id=? WHERE id=?",
            (sh_hex, cid, row_id),
        )
        clusters_seen.add(cid)
        processed += 1
    conn.commit()
    return processed, len(clusters_seen)


if __name__ == "__main__":
    reset = "--reset" in sys.argv
    n, c = backfill(reset=reset)
    print(f"backfilled {n} rows into {c} clusters (reset={reset})")
