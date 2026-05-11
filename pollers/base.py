"""Base utilities for pollers: HTTP session, logging, loop runner."""
from __future__ import annotations

import logging
import random
import sys
import time
from pathlib import Path

import requests

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.6 Safari/605.1.15"
)


def make_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s")

    fh = logging.FileHandler(LOG_DIR / f"{name}.log", encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    return logger


def make_session(extra_headers: dict | None = None) -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": DEFAULT_UA, "Accept": "*/*"})
    if extra_headers:
        s.headers.update(extra_headers)
    return s


def run_loop(name: str, interval: float, fetch_once_fn, logger: logging.Logger):
    """Generic loop: call fetch_once_fn(); on exception log + backoff."""
    backoff = interval
    while True:
        t0 = time.time()
        try:
            n = fetch_once_fn()
            if n is None:
                n = 0
            logger.info(f"tick ok, inserted={n}, took={time.time()-t0:.2f}s")
            backoff = interval
        except Exception as e:
            logger.error(f"tick failed: {e!r}; backoff={backoff:.1f}s")
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)
            continue
        # jitter to avoid thundering herd across pollers
        time.sleep(interval + random.uniform(0, 0.5))
