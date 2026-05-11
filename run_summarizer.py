"""LLM summary scheduler.

Cadence (tuned to fit Claude Pro subscription ≈ 45 msgs / 5h ≈ 9/hr):
  - 1h window:  every 15 min   (4/hr, captures fresh flow)
  - 5h window:  every 30 min   (2/hr, mid-term context)
  - 24h window: every 60 min   (1/hr, day overview)
                                ─────
                                7/hr, leaves 2 buffer for user's own usage

Cache: if the cluster_id set for a window hasn't changed since last call,
summarize_window returns the cached row without hitting the LLM (free).
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pollers.base import make_logger
from summarizer import summarize_window

logger = make_logger("summarizer")

# (window, period_seconds)
SCHEDULE = [
    ("1h", 15 * 60),
    ("5h", 30 * 60),
    ("24h", 60 * 60),
]


def main():
    last_run: dict[str, float] = {w: 0.0 for w, _ in SCHEDULE}
    logger.info(f"summarizer worker started, schedule={SCHEDULE}")

    while True:
        now = time.time()
        for window, period in SCHEDULE:
            if now - last_run[window] < period:
                continue
            try:
                res = summarize_window(window)
                cached = "(cached)" if res.get("cached") else ""
                logger.info(
                    f"window={window} clusters={res['cluster_count']} "
                    f"elapsed_ms={res.get('elapsed_ms', 0)} {cached}"
                )
            except Exception as e:
                logger.error(f"window={window} failed: {e!r}")
            last_run[window] = time.time()
        time.sleep(30)


if __name__ == "__main__":
    main()
