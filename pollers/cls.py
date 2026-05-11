"""Cailianpress (财联社) telegraph poller.

Note: cls.cn refuses to respond through this machine's local HTTP proxy
(127.0.0.1:7897), so we use curl with --noproxy '*'.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import connect, insert_many, NewsItem
from pollers.base import make_logger, run_loop, DEFAULT_UA

NAME = "cls"
URL = "https://www.cls.cn/nodeapi/telegraphList?app=CailianpressWeb&os=web&sv=7.7.5&rn=30"

logger = make_logger(NAME)
conn = connect()


LEVEL_MAP = {"A": 3, "B": 2, "C": 1}


def http_get_json(url: str, timeout: int = 10) -> dict:
    proc = subprocess.run(
        [
            "curl", "-sS", "--max-time", str(timeout),
            "--noproxy", "*",
            "-H", f"User-Agent: {DEFAULT_UA}",
            "-H", "Referer: https://www.cls.cn/",
            url,
        ],
        capture_output=True, text=True, timeout=timeout + 5,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"curl failed rc={proc.returncode}: {proc.stderr.strip()}")
    return json.loads(proc.stdout)


def fetch_once() -> int:
    payload = http_get_json(URL, timeout=10)
    if payload.get("error"):
        raise RuntimeError(f"cls api error: {payload.get('error')}")
    arr = (payload.get("data") or {}).get("roll_data") or []
    items: list[NewsItem] = []
    for d in arr:
        if d.get("is_ad") or d.get("is_fad"):
            continue
        ad = d.get("ad") or {}
        if isinstance(ad, dict) and ad.get("id"):
            continue
        content = d.get("content") or ""
        if not content:
            continue
        items.append(
            NewsItem(
                source=NAME,
                source_id=str(d.get("id")),
                ts=int(d.get("ctime") or 0),
                title=d.get("title") or content[:30],
                content=content,
                importance=LEVEL_MAP.get(d.get("level"), 1),
                tags=[s.get("subject_name") for s in (d.get("subjects") or []) if isinstance(s, dict)],
                url=d.get("shareurl") or "",
                raw=d,
            )
        )
    return insert_many(conn, items)


if __name__ == "__main__":
    if "--once" in sys.argv:
        n = fetch_once()
        logger.info(f"once: inserted={n}")
    else:
        run_loop(NAME, interval=3.0, fetch_once_fn=fetch_once, logger=logger)
