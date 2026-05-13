"""Background worker: re-export the public JSON every 60s.

Idempotent. If the cluster set hasn't changed we still rewrite the file
(cheap; ~80KB) so `generated_at` stays current and the frontend's freshness
indicator is accurate.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pollers.base import make_logger
from exporter import export_once

logger = make_logger("exporter")
INTERVAL_SEC = 60


def main():
    logger.info(f"exporter worker started, interval={INTERVAL_SEC}s")
    while True:
        t0 = time.time()
        try:
            res = export_once()
            logger.info(
                f"wrote clusters={res['clusters']} bytes={res['bytes']} "
                f"took={time.time()-t0:.2f}s"
            )
        except Exception as e:
            logger.error(f"export failed: {e!r}")
        time.sleep(max(0.0, INTERVAL_SEC - (time.time() - t0)))


if __name__ == "__main__":
    main()
