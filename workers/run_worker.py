"""Worker loop. Run with the BACKEND venv from the backend dir (sqlite path is relative):

    cd backend && .venv/bin/python ../workers/run_worker.py [--once] [--poll 3]

Heavy steps (face extraction, LivePortrait) are launched as subprocesses in workers/.venv.
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app import jobs  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="drain the queue then exit")
    ap.add_argument("--poll", type=float, default=3.0)
    a = ap.parse_args()
    deps = jobs.Deps()
    while True:
        try:
            res = jobs.run_once(deps)
        except Exception as e:  # keep the loop alive on unexpected errors
            print("worker error:", e, flush=True)
            res = None
        if res:
            print(f"[worker] {res[0]} {res[1]} -> {'ok' if res[2] else 'error'}", flush=True)
            continue
        if a.once:
            return
        time.sleep(a.poll)


if __name__ == "__main__":
    main()
