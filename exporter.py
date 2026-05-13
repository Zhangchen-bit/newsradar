"""B1 — Periodically export deduped feed as a static JSON file for the
BYOK frontend to consume directly (without hitting any API).

Output path: static_public/news.json

Schema (frontend contract):
{
  "generated_at": <unix_ts>,
  "window_hours": 24,
  "cluster_count": <int>,
  "clusters": [
    {
      "cluster_id": int, "ts": int, "importance": int,
      "source_count": int, "sources": [str],
      "title": str, "content": str, "tags": [str],
      "verified": bool   // public field; details NOT exposed
    },
    ...
  ]
}
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from query import feed

ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "static_public"
OUT_FILE = OUT_DIR / "news.json"

WINDOW_HOURS = 24
MAX_CLUSTERS = 200


def export_once(window_hours: int = WINDOW_HOURS) -> dict:
    """Generate the JSON snapshot. Returns metadata about what was written."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    since = int(time.time()) - window_hours * 3600
    items = feed(since_ts=since, min_importance=1, limit=MAX_CLUSTERS)

    public_clusters = []
    for it in items:
        sources = (it["sources"] or "").split(",") if it["sources"] else []
        public_clusters.append({
            "cluster_id": it["cluster_id"],
            "ts":         it["latest_ts"],
            "importance": it["importance"],
            "source_count": it["source_count"],
            "sources":    sources,
            "title":      it["title"] or "",
            "content":    it["content"] or "",
            # tags would require extra query; skipped to keep this lightweight
            "verified":   it.get("verify_status") == "confirmed",
        })

    latest_ts = max((c["ts"] for c in public_clusters), default=0)
    payload = {
        "generated_at": int(time.time()),
        "window_hours": window_hours,
        "cluster_count": len(public_clusters),
        "latest_ts": latest_ts,
        "is_stale": (latest_ts > 0 and (int(time.time()) - latest_ts) > 3600),
        "clusters": public_clusters,
    }

    # atomic write: tmp -> rename
    tmp = OUT_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                   encoding="utf-8")
    tmp.replace(OUT_FILE)

    return {
        "path": str(OUT_FILE),
        "bytes": OUT_FILE.stat().st_size,
        "clusters": len(public_clusters),
    }


if __name__ == "__main__":
    import sys
    wh = WINDOW_HOURS
    for arg in sys.argv[1:]:
        if arg.startswith("--window="):
            wh = int(arg.split("=", 1)[1])
    res = export_once(window_hours=wh)
    print(f"wrote {res['clusters']} clusters → {res['path']} ({res['bytes']} bytes)")
