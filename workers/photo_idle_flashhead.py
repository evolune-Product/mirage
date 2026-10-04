"""Photo -> idle clip (blinking, subtle head motion, mouth at rest) with SoulX-FlashHead: the photo is animated over SILENT audio.

Replaces LivePortrait/InsightFace (non-commercial weights) for photo avatars. Same CLI/JSON as photo_idle.py.
usage: photo_idle_flashhead.py --image photo.png --out idle.mp4 [--seconds 4]
Idle quality is unverified for long loops: the live service ping-pongs this clip, so it only needs to look calm."""
import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import render_flashhead as rf  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--seconds", type=float, default=4.0)
    ap.add_argument("--head", type=float, default=1.0)  # accepted for CLI compatibility
    a = ap.parse_args()
    ok, why = rf.available()
    if not ok:
        print(json.dumps({"ok": False, "error": f"flashhead unavailable ({why})"}))
        return 1
    t0 = time.time()
    with tempfile.TemporaryDirectory() as td:
        sil = Path(td) / "silence.wav"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono", "-t",
                        str(max(a.seconds, 2.0)), str(sil)], check=True)
        raw = Path(td) / "idle_raw.mp4"
        r = subprocess.run([str(rf.PY), str(HERE / "render_flashhead.py"), "--image", a.image, "--audio", str(sil), "--out", str(raw)],
                           capture_output=True, text=True)
        try:
            res = json.loads(r.stdout.strip().splitlines()[-1])
        except Exception:
            res = {"ok": False, "error": (r.stderr or r.stdout)[-400:]}
        if not res.get("ok") or not raw.exists():
            print(json.dumps({"ok": False, "error": res.get("error", "flashhead idle failed")}))
            return 1
        m = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw), "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                            "-crf", "16", a.out], capture_output=True, text=True)  # silent loop source
        if m.returncode:
            print(json.dumps({"ok": False, "error": "mux failed: " + m.stderr[-300:]}))
            return 1
    print(json.dumps({"ok": True, "seconds": round(time.time() - t0, 1), "engine": "flashhead", "out_frames": int(a.seconds * 25)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
