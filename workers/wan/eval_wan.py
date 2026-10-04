"""Identity / motion metrics for a Wan clip. Run with workers/.venv-face/bin/python eval_wan.py clip.mp4 source.png [outdir]"""
import sys, json, os
import numpy as np, cv2
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import facelib as fl
M = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "models") + "/"
clip, src = sys.argv[1], sys.argv[2]; outdir = sys.argv[3] if len(sys.argv) > 3 else None
tr = fl.FaceTracker()
det = cv2.FaceDetectorYN.create(M + "yunet.onnx", "", (320, 320)); rec = cv2.FaceRecognizerSF.create(M + "sface.onnx", "")
def emb(img):
    h, w = img.shape[:2]; det.setInputSize((w, h)); _, f = det.detect(img)
    return None if f is None else rec.feature(rec.alignCrop(img, f[0]))
ref = emb(cv2.imread(src))
cap = cv2.VideoCapture(clip); fr = []
while True:
    ok, f = cap.read()
    if not ok: break
    fr.append(f)
obs = [tr(f) for f in fr]
sims = [float(rec.match(ref, e, cv2.FaceRecognizerSF_FR_COSINE)) for f in fr if (e := emb(f)) is not None]
jaw = np.array([o.jaw_open if o.ok else np.nan for o in obs])
cen = np.array([[o.box[0] + o.box[2] / 2, o.box[1] + o.box[3] / 2] for o in obs if o.ok]); wd = np.mean([o.box[2] for o in obs if o.ok]) if len(cen) else 1
jit = float(np.mean(np.linalg.norm(np.diff(cen, axis=0), axis=1)) / wd) if len(cen) > 2 else None
diff = [float(np.abs(a.astype(np.float32) - b.astype(np.float32)).mean()) for a, b in zip(fr[:-1], fr[1:])]
res = dict(frames=len(fr), size=f"{fr[0].shape[1]}x{fr[0].shape[0]}", fps=cap.get(cv2.CAP_PROP_FPS), face_found=round(float(np.mean([o.ok for o in obs])), 3),
           identity_cos_mean=round(float(np.mean(sims)), 3), identity_cos_min=round(float(np.min(sims)), 3), identity_cos_last=round(sims[-1], 3), n_emb=len(sims),
           jaw_range=round(float(np.nanpercentile(jaw, 95) - np.nanpercentile(jaw, 5)), 3), head_jitter=None if jit is None else round(jit, 4),
           mean_frame_diff=round(float(np.mean(diff)), 2), max_frame_diff=round(float(np.max(diff)), 2))
print(json.dumps(res))
if outdir:
    os.makedirs(outdir, exist_ok=True)
    idx = np.linspace(0, len(fr) - 1, 6).astype(int)
    s = cv2.resize(cv2.imread(src), (fr[0].shape[1], fr[0].shape[0]))
    cv2.imwrite(os.path.join(outdir, "strip.png"), np.hstack([s] + [fr[i] for i in idx]))
