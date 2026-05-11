"""FastAPI backend for News Radar.

Endpoints:
  GET  /api/news?since=<ts>&min_importance=<n>&limit=<n>
  GET  /api/cluster/{cluster_id}    — all source rows for one cluster
  GET  /api/summary?window=1h|5h|24h
  GET  /api/stream                  — SSE: emits a JSON line whenever new
                                      cluster ids appear or new summaries land
  GET  /                            — static frontend
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from db import connect
from query import feed, latest_summary

ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"

app = FastAPI(title="News Radar")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/news")
def api_news(
    since: int | None = None,
    min_importance: int = Query(1, ge=0, le=3),
    limit: int = Query(50, ge=1, le=200),
):
    """Deduped cluster feed."""
    items = feed(since_ts=since, min_importance=min_importance, limit=limit)
    return {"count": len(items), "items": items, "server_ts": int(time.time())}


@app.get("/api/cluster/{cluster_id}")
def api_cluster(cluster_id: int):
    """All source rows in one cluster."""
    conn = connect()
    rows = conn.execute(
        """SELECT source, source_id, ts, title, content, importance, url, tags
           FROM news WHERE cluster_id=? ORDER BY ts ASC""",
        (cluster_id,),
    ).fetchall()
    if not rows:
        raise HTTPException(status_code=404, detail="cluster not found")
    cols = ["source", "source_id", "ts", "title", "content",
            "importance", "url", "tags"]
    items = [dict(zip(cols, r)) for r in rows]
    for it in items:
        try:
            it["tags"] = json.loads(it["tags"] or "[]")
        except Exception:
            it["tags"] = []
    v = conn.execute(
        "SELECT status, company, keyword, evidence, evidence_date "
        "FROM verifications WHERE cluster_id=?",
        (cluster_id,),
    ).fetchone()
    return {
        "cluster_id": cluster_id,
        "items": items,
        "verification": (
            dict(zip(["status", "company", "keyword", "evidence", "evidence_date"], v))
            if v else None
        ),
    }


@app.get("/api/summary")
def api_summary(window: str = Query(..., pattern="^(1h|5h|24h)$")):
    s = latest_summary(window)
    if not s:
        return {"window": window, "topics": {}, "generated_at": None,
                "cluster_count": 0}
    return s


@app.get("/api/summaries")
def api_summaries():
    """Bundle 1h+5h+24h in one call."""
    return {w: latest_summary(w) for w in ("1h", "5h", "24h")}


# ---- SSE -------------------------------------------------------------------


async def _event_stream():
    """Emit `news` event when cluster set changes, `summary` event when a new
    summary row is inserted."""
    conn = connect()
    last_max_cluster = 0
    last_summary_id = 0

    # initialize
    row = conn.execute("SELECT COALESCE(MAX(cluster_id), 0) FROM news").fetchone()
    last_max_cluster = int(row[0])
    row = conn.execute("SELECT COALESCE(MAX(id), 0) FROM summaries").fetchone()
    last_summary_id = int(row[0])

    # initial ping
    yield f"event: hello\ndata: {json.dumps({'ts': int(time.time())})}\n\n"

    while True:
        await asyncio.sleep(3)
        try:
            row = conn.execute(
                "SELECT COALESCE(MAX(cluster_id), 0) FROM news"
            ).fetchone()
            mc = int(row[0])
            if mc > last_max_cluster:
                last_max_cluster = mc
                # emit the newest 5 clusters as a snapshot
                items = feed(since_ts=int(time.time()) - 6 * 3600,
                             min_importance=1, limit=5)
                yield f"event: news\ndata: {json.dumps({'items': items}, ensure_ascii=False)}\n\n"

            row = conn.execute(
                "SELECT COALESCE(MAX(id), 0) FROM summaries"
            ).fetchone()
            sid = int(row[0])
            if sid > last_summary_id:
                last_summary_id = sid
                # emit which window updated
                w = conn.execute(
                    "SELECT window FROM summaries WHERE id=?", (sid,)
                ).fetchone()
                yield (
                    f"event: summary\n"
                    f"data: {json.dumps({'window': w[0] if w else ''})}\n\n"
                )

            # keepalive
            yield f": keepalive {int(time.time())}\n\n"
        except Exception as e:
            yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"
            await asyncio.sleep(5)


@app.get("/api/stream")
async def api_stream():
    return StreamingResponse(_event_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


# ---- static frontend -------------------------------------------------------

if STATIC_DIR.exists():
    @app.get("/")
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
