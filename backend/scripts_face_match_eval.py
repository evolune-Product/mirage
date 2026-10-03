"""Measure SFace same-person vs different-person cosine scores on LOCAL footage to pick the consent face threshold.

  python backend/scripts_face_eval.py out.json   (runs workers/face_embed.py in workers/.venv-face for every media file)

Genuine pairs: frames of the same video/identity far apart in time. Impostor pairs: frames from different identities
(owner videos vs third-party example photos/videos shipped inside workers/LivePortrait and workers/MuseTalk; used for
measurement only on this machine, never redistributed). Prints percentile tables."""
import glob, json, subprocess, sys
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
PY = ROOT / "workers/.venv-face/bin/python"
SP = Path.home() / "Desktop/SpendVeto Video and PPT"
D = Path.home() / "Desktop"
MEDIA = {  # identity -> list of (file, start, duration)
    "owner": [(SP / "SpendVeto_Founder_Video_1min.mp4", 0, 30), (SP / "SpendVeto_Founder_Video_1min.mp4", 30, 30),
              (D / "0629.mp4", 0, 0), (D / "1002.mp4", 0, 0)],
}
for f in sorted(glob.glob(str(ROOT / "workers/LivePortrait/assets/examples/source/*.jpg"))) + \
         sorted(glob.glob(str(ROOT / "workers/MuseTalk/assets/demo/*/*.png"))):
    MEDIA[Path(f).stem + "_" + Path(f).parent.name] = [(Path(f), 0, 0)]
for f in sorted(glob.glob(str(ROOT / "workers/LivePortrait/assets/examples/driving/*.mp4"))) + \
         sorted(glob.glob(str(ROOT / "workers/LivePortrait/assets/examples/source/*.mp4"))) + \
         [str(ROOT / "workers/MuseTalk/data/video/sun.mp4"), str(ROOT / "workers/MuseTalk/data/video/yongen.mp4")]:
    MEDIA["vid_" + Path(f).stem] = [(Path(f), 0, 0)]


def run(f, start, dur):
    c = [str(PY), str(ROOT / "workers/face_embed.py"), "--media", str(f), "--fps", "2", "--max-frames", "40"]
    if start: c += ["--start", str(start)]
    if dur: c += ["--duration", str(dur)]
    out = subprocess.run(c, capture_output=True, text=True).stdout.strip().splitlines()
    return json.loads(out[-1]) if out else {"ok": False}


if __name__ == "__main__":
    data = {}
    for ident, items in MEDIA.items():
        for i, (f, s, d) in enumerate(items):
            r = run(f, s, d)
            if r.get("ok") and r["frames"]:
                data[f"{ident}#{i}"] = {"ident": ident, "emb": [fr["emb"] for fr in r["frames"]], "live": r["liveness"]}
    Path(sys.argv[1]).write_text(json.dumps(data))
    print(len(data), "clips with faces")
