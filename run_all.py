"""Launch all pollers as subprocesses. Prints unified output, restarts on crash."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PY = sys.executable
POLLERS = ["jin10", "wscn", "cls"]
WORKERS = ["run_verifier", "run_summarizer", "run_api"]  # standalone scripts under ROOT


def main():
    procs: dict[str, subprocess.Popen] = {}

    def spawn(name: str, script_path: Path):
        p = subprocess.Popen(
            [PY, "-u", str(script_path)],
            cwd=ROOT,
            stdout=sys.stdout,
            stderr=sys.stdout,
        )
        procs[name] = p
        print(f"[run_all] spawned {name} pid={p.pid}", flush=True)

    for n in POLLERS:
        spawn(n, ROOT / "pollers" / f"{n}.py")
    for w in WORKERS:
        spawn(w, ROOT / f"{w}.py")

    def shutdown(signum, frame):
        print("[run_all] shutting down...", flush=True)
        for n, p in procs.items():
            try:
                p.terminate()
            except Exception:
                pass
        for n, p in procs.items():
            try:
                p.wait(timeout=5)
            except Exception:
                p.kill()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    while True:
        time.sleep(3)
        for n, p in list(procs.items()):
            if p.poll() is not None:
                print(f"[run_all] {n} died rc={p.returncode}, restarting in 5s", flush=True)
                time.sleep(5)
                if n in POLLERS:
                    spawn(n, ROOT / "pollers" / f"{n}.py")
                else:
                    spawn(n, ROOT / f"{n}.py")


if __name__ == "__main__":
    main()
