"""SQLite layer for News Radar. Single connection per process; pollers each open own."""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "news.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS news (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    ts INTEGER NOT NULL,
    title TEXT,
    content TEXT NOT NULL,
    importance INTEGER DEFAULT 1,
    tags TEXT,
    url TEXT,
    simhash TEXT,
    cluster_id INTEGER,
    raw TEXT,
    created_at INTEGER NOT NULL,
    UNIQUE(source, source_id)
);
CREATE INDEX IF NOT EXISTS idx_news_ts ON news(ts DESC);
CREATE INDEX IF NOT EXISTS idx_news_source_ts ON news(source, ts DESC);

CREATE TABLE IF NOT EXISTS summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    window TEXT NOT NULL,          -- '1h' / '5h' / '24h'
    generated_at INTEGER NOT NULL, -- unix ts
    cluster_hash TEXT NOT NULL,    -- md5(sorted cluster_ids) for cache
    cluster_count INTEGER NOT NULL,
    model TEXT NOT NULL,
    topics_json TEXT NOT NULL,     -- {topic: [points...]}
    input_summary TEXT,            -- compact description of what we fed in
    elapsed_ms INTEGER
);
CREATE INDEX IF NOT EXISTS idx_summaries_window_ts ON summaries(window, generated_at DESC);

CREATE TABLE IF NOT EXISTS verifications (
    cluster_id INTEGER PRIMARY KEY,
    status TEXT NOT NULL,        -- confirmed | partial | unconfirmed | skip | error
    company TEXT,                -- extracted entity (name or code)
    keyword TEXT,                -- event keyword that triggered
    query TEXT,                  -- query string sent to iFinD
    evidence TEXT,               -- top-1 notice/news title or empty
    evidence_date TEXT,          -- YYYY-MM-DD of evidence if any
    raw TEXT,                    -- truncated raw response
    checked_at INTEGER NOT NULL
);
"""


@dataclass
class NewsItem:
    source: str
    source_id: str
    ts: int
    content: str
    title: str = ""
    importance: int = 1
    tags: list = field(default_factory=list)
    url: str = ""
    raw: dict = field(default_factory=dict)


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.executescript(SCHEMA)
    return conn


def insert_many(conn: sqlite3.Connection, items: list[NewsItem]) -> int:
    """Insert with UNIQUE(source, source_id); compute simhash + cluster_id
    per row. Returns number actually inserted."""
    if not items:
        return 0
    # local import to avoid circular import at module load time
    from dedup import assign_cluster

    now = int(time.time())
    inserted = 0
    for it in items:
        # skip if already present (cheap pre-check; UNIQUE would catch anyway
        # but we want to avoid burning cluster_ids on duplicates)
        existing = conn.execute(
            "SELECT 1 FROM news WHERE source=? AND source_id=? LIMIT 1",
            (it.source, str(it.source_id)),
        ).fetchone()
        if existing:
            continue
        simhash_hex, cluster_id = assign_cluster(
            conn, int(it.ts), it.content, importance=it.importance
        )
        cur = conn.execute(
            """INSERT OR IGNORE INTO news
               (source, source_id, ts, title, content, importance, tags, url,
                simhash, cluster_id, raw, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                it.source,
                str(it.source_id),
                int(it.ts),
                (it.title or it.content[:30]),
                it.content,
                int(it.importance),
                json.dumps(it.tags, ensure_ascii=False),
                it.url,
                simhash_hex,
                cluster_id,
                json.dumps(it.raw, ensure_ascii=False, default=str),
                now,
            ),
        )
        inserted += cur.rowcount
    conn.commit()
    return inserted


def recent(conn: sqlite3.Connection, source: str | None = None, limit: int = 5) -> list[dict]:
    q = "SELECT source, ts, title, content, importance FROM news"
    params: tuple = ()
    if source:
        q += " WHERE source = ?"
        params = (source,)
    q += " ORDER BY ts DESC LIMIT ?"
    cur = conn.execute(q, (*params, limit))
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


if __name__ == "__main__":
    c = connect()
    print(f"DB initialized at {DB_PATH}")
    print("Tables:", [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")])
