"""Background worker: scan eligible clusters every 60s, verify up to 5 per pass.

Designed to run alongside run_all.py. Each iFinD call is ~1-3s; we cap at
5/pass and sleep 60s between passes to stay well within sane API limits.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pollers.base import make_logger
from verifier import verify_batch

logger = make_logger("verifier")

INTERVAL_SEC = 60
BATCH_SIZE = 5
SLEEP_BETWEEN_CALLS = 2.0


def main():
    logger.info(f"verifier worker started, interval={INTERVAL_SEC}s, batch={BATCH_SIZE}")
    while True:
        t0 = time.time()
        try:
            results = verify_batch(max_n=BATCH_SIZE, sleep_between=SLEEP_BETWEEN_CALLS)
            if results:
                counts: dict[str, int] = {}
                for r in results:
                    counts[r["status"]] = counts.get(r["status"], 0) + 1
                logger.info(f"verified {len(results)}: {counts}")
            else:
                logger.info("no eligible clusters this pass")
        except Exception as e:
            logger.error(f"pass failed: {e!r}")
        elapsed = time.time() - t0
        time.sleep(max(0.0, INTERVAL_SEC - elapsed))


if __name__ == "__main__":
    main()
