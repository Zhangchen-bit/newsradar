"""Wallstreetcn (华尔街见闻) live news poller.

Note: api-one.wallstreetcn.com sits behind a CDN that rejects Python's default
TLS fingerprint (handshake EOF). curl works fine, so we shell out to curl.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import connect, insert_many, NewsItem
from pollers.base import make_logger, run_loop, DEFAULT_UA

NAME = "wscn"
URLS = [
    "https://api-one-wscn.awtmt.com/apiv1/content/lives?channel=global-channel&client=pc&limit=30",
    "https://api-one.wallstreetcn.com/apiv1/content/lives?channel=global-channel&client=pc&limit=30",
]

logger = make_logger(NAME)
conn = connect()


def http_get_json(url: str, timeout: int = 10) -> dict:
    proc = subprocess.run(
        [
            "curl", "-sS", "--max-time", str(timeout),
            "-H", f"User-Agent: {DEFAULT_UA}",
            "-H", "Referer: https://wallstreetcn.com/",
            "-H", "Origin: https://wallstreetcn.com",
            url,
        ],
        capture_output=True, text=True, timeout=timeout + 5,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"curl failed rc={proc.returncode}: {proc.stderr.strip()}")
    return json.loads(proc.stdout)


def map_importance(score: int) -> int:
    if score is None:
        return 1
    if score >= 3:
        return 3
    if score >= 1:
        return 2
    return 1


def fetch_once() -> int:
    last_err = None
    payload = None
    for url in URLS:
        try:
            payload = http_get_json(url, timeout=10)
            break
        except Exception as e:
            last_err = e
            logger.warning(f"endpoint {url} failed: {e!r}")
    if payload is None:
        raise RuntimeError(f"all wscn endpoints failed: {last_err!r}")
    items_raw = (payload.get("data") or {}).get("items") or []
    items: list[NewsItem] = []
    for d in items_raw:
        content = d.get("content_text") or d.get("content") or ""
        if not content:
            continue
        items.append(
            NewsItem(
                source=NAME,
                source_id=str(d.get("id")),
                ts=int(d.get("display_time") or 0),
                title=d.get("title") or content[:30],
                content=content,
                importance=map_importance(d.get("score") or 0),
                tags=[c.get("name") for c in (d.get("channels") or []) if isinstance(c, dict)],
                url=d.get("uri") or "",
                raw=d,
            )
        )
    return insert_many(conn, items)


if __name__ == "__main__":
    if "--once" in sys.argv:
        n = fetch_once()
        logger.info(f"once: inserted={n}")
    else:
        run_loop(NAME, interval=5.0, fetch_once_fn=fetch_once, logger=logger)
