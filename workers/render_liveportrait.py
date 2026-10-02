"""Render a talking clip: face image + speech wav -> mp4 (with audio). Runs in workers/.venv.

Pipeline: audio_driver builds a mouth-motion template from the audio loudness envelope ->
LivePortrait animates the replica face with it -> ffmpeg muxes the original speech.
APPROXIMATE lip-sync: mouth opens/closes with loudness, no phonemes (see audio_driver.py).
Speed on M1 (MPS): ~0.5 fps rendered, so 10 s of speech at 12 fps ~ 4 min. Offline only.
usage: render_liveportrait.py --image face.png --audio speech.wav --out out.mp4 [--fps 12]
"""
import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import audio_driver  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True)
    ap.add_argument("--audio", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--fps", type=float, default=12.0)
    a = ap.parse_args()
    t0 = time.time()
    work = tempfile.mkdtemp(prefix="lp_")
    try:
        tpl = os.path.join(work, "drive.pkl")
        info = audio_driver.make_template(a.audio, tpl, fps=a.fps)
        env = dict(os.environ, PYTORCH_ENABLE_MPS_FALLBACK="1")
        r = subprocess.run([sys.executable, "inference.py", "-s", a.image, "-d", tpl, "-o", work],
                           cwd=HERE / "LivePortrait", env=env, capture_output=True, text=True)
        vids = [v for v in glob.glob(os.path.join(work, "*.mp4")) if not v.endswith("_concat.mp4")]
        if r.returncode != 0 or not vids:
            print(json.dumps({"ok": False, "error": (r.stderr or r.stdout)[-800:]}))
            sys.exit(1)
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        m = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", vids[0], "-i", a.audio, "-map", "0:v", "-map", "1:a",
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", a.out],
                           capture_output=True, text=True)
        if m.returncode != 0:
            print(json.dumps({"ok": False, "error": "mux failed: " + m.stderr[-400:]}))
            sys.exit(1)
        print(json.dumps({"ok": True, "seconds": round(time.time() - t0, 1), **info}))
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
