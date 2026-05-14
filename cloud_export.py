"""Standalone exporter for GitHub Actions / cloud cron.

Pipeline:
  1. Fetch from jin10 / wscn / cls in parallel (best-effort; partial OK)
  2. In-memory SimHash dedup + cluster assignment
  3. Write news.json to the target path

Differences from local-mode exporter.py:
  * No SQLite (state-free, suitable for ephemeral CI)
  * Cluster IDs are not stable across runs — frontend treats them as opaque
  * No iFinD verification (skipped in public version)
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

from dedup import simhash, hamming, CLUSTER_HAMMING_THRESHOLD, CLUSTER_WINDOW_SEC
from filters import is_noise

# Lazy import to keep startup snappy and let partial failures isolate
SOURCES = {
    "jin10": "pollers.jin10",
    "wscn":  "pollers.wscn",
    "cls":   "pollers.cls",
}


def fetch_all(timeout: float = 25.0) -> tuple[list, dict]:
    """Run all source fetchers in parallel. Returns (items, per_source_status)."""
    import importlib

    def _fetch_one(name: str, modname: str):
        mod = importlib.import_module(modname)
        return name, mod.fetch_items()

    items = []
    status: dict[str, dict] = {}
    with cf.ThreadPoolExecutor(max_workers=len(SOURCES)) as ex:
        futs = {ex.submit(_fetch_one, n, m): n for n, m in SOURCES.items()}
        for fut in cf.as_completed(futs, timeout=timeout):
            name = futs[fut]
            try:
                _, got = fut.result()
                items.extend(got)
                status[name] = {"ok": True, "count": len(got)}
            except Exception as e:
                status[name] = {"ok": False, "error": repr(e)[:200]}
    return items, status


def cluster_in_memory(items: list) -> list[dict]:
    """Group items into clusters using simhash + time-window. Returns list of
    cluster dicts ready for JSON.
    """
    # work oldest → newest for stable cluster seeding
    items = sorted(items, key=lambda i: i.ts)
    # parallel arrays: items[i], hashes[i], cluster_of[i]
    hashes = [simhash(it.content) for it in items]
    cluster_of: list[int | None] = [None] * len(items)
    next_cid = 0
    for i, it in enumerate(items):
        # search backward for a close hash within time window
        best_cid: int | None = None
        best_dist = CLUSTER_HAMMING_THRESHOLD + 1
        for j in range(i - 1, -1, -1):
            if it.ts - items[j].ts > CLUSTER_WINDOW_SEC:
                break  # sorted by ts, can stop
            d = hamming(hashes[i], hashes[j])
            if d < best_dist:
                best_dist = d
                best_cid = cluster_of[j]
                if d == 0:
                    break
        if best_cid is not None and best_dist <= CLUSTER_HAMMING_THRESHOLD:
            cluster_of[i] = best_cid
        else:
            next_cid += 1
            cluster_of[i] = next_cid

    # roll up clusters
    clusters: dict[int, dict] = {}
    for it, cid in zip(items, cluster_of):
        c = clusters.setdefault(cid, {
            "cluster_id": cid,
            "ts": it.ts,
            "importance": it.importance,
            "sources": set(),
            "source_ids": [],   # for debug, not exposed
            "content": it.content,
            "title": it.title,
            "tags": list(it.tags or []),
        })
        c["sources"].add(it.source)
        c["source_ids"].append(f"{it.source}:{it.source_id}")
        if it.importance > c["importance"]:
            c["importance"] = it.importance
        if it.ts > c["ts"]:
            c["ts"] = it.ts
        # use longest content as representative
        if len(it.content) > len(c["content"]):
            c["content"] = it.content
            c["title"] = it.title

    out = []
    for cid, c in clusters.items():
        out.append({
            "cluster_id": cid,
            "ts": c["ts"],
            "importance": c["importance"],
            "source_count": len(c["sources"]),
            "sources": sorted(c["sources"]),
            "title": c["title"] or "",
            "content": c["content"],
            "tags": c["tags"],
            "verified": False,   # cloud version never carries iFinD
        })
    out.sort(key=lambda c: (-c["importance"], -c["ts"]))
    return out


def export(out_path: Path, window_hours: int = 24,
           max_clusters: int = 200) -> dict:
    t0 = time.time()
    raw_items, status = fetch_all()

    # filter to window
    cutoff = int(time.time()) - window_hours * 3600
    items_in_win = [it for it in raw_items if it.ts >= cutoff]

    # drop promo / roundup noise BEFORE clustering so they don't anchor
    # otherwise-clean clusters
    dropped = {"promo": 0, "roundup": 0}
    kept = []
    for it in items_in_win:
        drop, reason = is_noise(it.content)
        if drop:
            dropped[reason] = dropped.get(reason, 0) + 1
        else:
            kept.append(it)
    status["_filter_dropped"] = dropped

    clusters = cluster_in_memory(kept)[:max_clusters]

    latest_ts = max((c["ts"] for c in clusters), default=0)
    payload = {
        "generated_at": int(time.time()),
        "window_hours": window_hours,
        "cluster_count": len(clusters),
        "latest_ts": latest_ts,
        # Mark stale only if latest news is older than 90 min — GitHub cron
        # can lag 15-30 min so 60 min is too tight, would falsely flag.
        "is_stale": (latest_ts > 0 and (int(time.time()) - latest_ts) > 5400),
        "source_status": status,
        "clusters": clusters,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                   encoding="utf-8")
    tmp.replace(out_path)

    return {
        "elapsed_s": round(time.time() - t0, 2),
        "raw_items": len(raw_items),
        "in_window": len(items_in_win),
        "clusters": len(clusters),
        "status": status,
        "bytes": out_path.stat().st_size,
        "path": str(out_path),
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="news.json")
    ap.add_argument("--window", type=int, default=24)
    args = ap.parse_args()
    res = export(Path(args.out), window_hours=args.window)
    print(json.dumps(res, ensure_ascii=False, indent=2))
