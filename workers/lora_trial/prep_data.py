"""Build 512x512 / 25 fps tight face crops (side 2.3x face width, centre +0.12 face height, like wa_tight.png) from the owner's
own footage and split by time. Run with workers/.venv-face/bin/python. Outputs under SoulX-FlashHead/lora/data (git-ignored)."""
import subprocess, sys, json
from pathlib import Path
import numpy as np, cv2
W = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(W))
import facelib as fl
OUT = W / "SoulX-FlashHead/lora/data"; OUT.mkdir(parents=True, exist_ok=True)
FOUNDER = Path.home() / "Desktop/SpendVeto Video and PPT/SpendVeto_Founder_Video_1min.mp4"
DEMO = W.parent / "demo_assets/demo_face_v2.mp4"
HELD = 10.0   # last 10 s of the founder speech is held out; 1 s gap before it is dropped from training

def read(p):
    cap = cv2.VideoCapture(str(p)); fr = []
    while True:
        ok, f = cap.read()
        if not ok: break
        fr.append(f)
    return fr

def crops(frames, tr):
    obs = [tr(f) for f in frames]
    ok = np.array([o.ok for o in obs]); print("face found", ok.mean())
    cx = np.array([o.box[0] + o.box[2] / 2 if o.ok else np.nan for o in obs]); cy = np.array([o.box[1] + o.box[3] / 2 if o.ok else np.nan for o in obs])
    w = np.array([o.box[2] if o.ok else np.nan for o in obs]); h = np.array([o.box[3] if o.ok else np.nan for o in obs])
    idx = np.arange(len(frames))
    def fill(a): return np.interp(idx, idx[~np.isnan(a)], a[~np.isnan(a)])
    cx, cy, w, h = [fl.smooth_series(fill(a), 4.0) if a is not cx else fl.smooth_series(fill(a), 4.0) for a in (cx, cy, w, h)]
    out = []
    for f, x, y, ww, hh in zip(frames, cx, cy, w, h):
        side = 2.3 * ww; yc = y + 0.12 * hh
        M = np.array([[512 / side, 0, -(x - side / 2) * 512 / side], [0, 512 / side, -(yc - side / 2) * 512 / side]], np.float32)
        out.append(cv2.cvtColor(cv2.warpAffine(f, M, (512, 512), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REFLECT), cv2.COLOR_BGR2RGB))
    return np.stack(out)

def wav(src, dst, t0, t1):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(t0), "-t", str(t1 - t0), "-i", str(src), "-vn", "-ac", "1", "-ar", "16000", str(dst)], check=True)

tr = fl.FaceTracker()
fr = read(FOUNDER); n = len(fr); print("founder frames", n)
c = crops(fr, tr)
nh = int(HELD * 25); v0 = n - nh; t_end = (v0 - 25) / 25
np.save(OUT / "founder_train.npy", c[: v0 - 25]); np.save(OUT / "founder_val.npy", c[v0:])
wav(FOUNDER, OUT / "founder_train.wav", 0, (v0 - 25) / 25); wav(FOUNDER, OUT / "heldout.wav", v0 / 25, n / 25)
fr2 = read(DEMO); c2 = crops(fr2, tr)
np.save(OUT / "demo_train.npy", c2); wav(DEMO, OUT / "demo_train.wav", 0, len(fr2) / 25)
json.dump(dict(founder_train_frames=v0 - 25, founder_val_frames=nh, demo_frames=len(fr2)), open(OUT / "split.json", "w"))
for name, arr in [("f_tr", c[100]), ("f_val", c[v0 + 50]), ("demo", c2[100])]:
    cv2.imwrite(str(OUT / f"{name}.png"), cv2.cvtColor(arr, cv2.COLOR_RGB2BGR))
cv2.imwrite(str(OUT / "montage.png"), cv2.cvtColor(np.hstack([c[100], c[v0 + 50], c2[100], cv2.resize(cv2.imread("/tmp/mirage_demo/wa_tight.png"), (512, 512))[:, :, ::-1]]), cv2.COLOR_RGB2BGR))
print("done", json.load(open(OUT / "split.json")))
