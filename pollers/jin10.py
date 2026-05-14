"""Jin10 flash news poller. Uses public flash-api endpoint."""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import connect, insert_many, NewsItem
from pollers.base import make_logger, make_session, run_loop

NAME = "jin10"
URL = "https://flash-api.jin10.com/get_flash_list"
HEADERS = {
    "x-app-id": "bVBF4FyRTn5NJF5n",
    "x-version": "1.0.0",
    "Origin": "https://www.jin10.com",
    "Referer": "https://www.jin10.com/",
}

logger = make_logger(NAME)
session = make_session(HEADERS)


def parse_ts(s: str) -> int:
    # jin10 returns "2025-05-11 09:12:34" in Beijing time (no TZ suffix).
    # Anchor to Asia/Shanghai so the resulting epoch is correct regardless of
    # where this code runs (local macOS = UTC+8, GitHub runner = UTC).
    from datetime import datetime
    from zoneinfo import ZoneInfo
    naive = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    return int(naive.replace(tzinfo=ZoneInfo("Asia/Shanghai")).timestamp())


def fetch_items() -> list[NewsItem]:
    """Pull + parse; no DB side-effect. Used by both fetch_once and cloud_export."""
    r = session.get(URL, params={"channel": "-8200", "vip": "1"}, timeout=10)
    r.raise_for_status()
    payload = r.json()
    data = payload.get("data") or []
    items: list[NewsItem] = []
    for d in data:
        # type=1 is flash news; skip non-flash (e.g. type=2 articles) for now
        if d.get("type") not in (0, 1):
            continue
        body = d.get("data") or {}
        content = body.get("content") or body.get("title") or ""
        if not content:
            continue
        important = int(d.get("important") or 0)
        items.append(
            NewsItem(
                source=NAME,
                source_id=str(d.get("id")),
                ts=parse_ts(d["time"]),
                title=body.get("title") or content[:30],
                content=content,
                importance=3 if important else 1,
                tags=[t.get("name") for t in (d.get("tags") or []) if isinstance(t, dict)],
                url=body.get("link") or "",
                raw=d,
            )
        )
    return items


def fetch_once() -> int:
    items = fetch_items()
    return insert_many(connect(), items)


if __name__ == "__main__":
    if "--once" in sys.argv:
        n = fetch_once()
        logger.info(f"once: inserted={n}")
    else:
        run_loop(NAME, interval=5.0, fetch_once_fn=fetch_once, logger=logger)
