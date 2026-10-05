"""Metrics as in /tmp/mirage_demo/evalface.py (SFace identity cos vs reference photo, jaw range, lip-audio corr [weak proxy], sharpness, jitter)
plus paired PSNR/SSIM against the REAL held-out footage crop. Run with workers/.venv-face/bin/python.
usage: evalpair.py REF.png OUT.json name=video.mp4 ... [--gt gt.npy]"""
import sys, json, subprocess
import numpy as np, cv2
from pathlib import Path
W = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(W))
import facelib as fl
def ssim(a, b, channel_axis=2):
    a = a.astype(np.float64); b = b.astype(np.float64); C1, C2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    g = lambda x: cv2.GaussianBlur(x, (11, 11), 1.5)[5:-5, 5:-5]
    ma, mb = g(a), g(b); va, vb, cab = g(a * a) - ma * ma, g(b * b) - mb * mb, g(a * b) - ma * mb
    return float(np.mean(((2 * ma * mb + C1) * (2 * cab + C2)) / ((ma * ma + mb * mb + C1) * (va + vb + C2))))  # gaussian-window SSIM
M = str(W.parent / "models") + "/"
args = sys.argv[1:]; gt = None
if "--gt" in args: i = args.index("--gt"); gt = np.load(args[i + 1]); del args[i:i + 2]
ref_path, out_path, vids = args[0], args[1], args[2:]
tr = fl.FaceTracker(); det = cv2.FaceDetectorYN.create(M + "yunet.onnx", "", (320, 320)); rec = cv2.FaceRecognizerSF.create(M + "sface.onnx", "")
def emb(img):
    h, w = img.shape[:2]; det.setInputSize((w, h)); _, f = det.detect(img)
    return None if f is None else rec.feature(rec.alignCrop(img, f[0]))
ref = emb(cv2.imread(ref_path))
def frames(p):
    cap = cv2.VideoCapture(p); o = []
    while True:
        ok, f = cap.read()
        if not ok: break
        o.append(f)
    return o
def env(p, n):
    a = np.frombuffer(subprocess.run(["ffmpeg", "-v", "error", "-i", p, "-vn", "-ac", "1", "-ar", "16000", "-f", "f32le", "-"], capture_output=True).stdout, np.float32)
    return np.array([np.sqrt((a[i * 640:(i + 1) * 640] ** 2).mean() + 1e-12) for i in range(n)])
zs = lambda x: (x - np.nanmean(x)) / (np.nanstd(x) + 1e-9)
res = {}
for kv in vids:
    name, p = kv.split("=", 1); fr = frames(p); n = len(fr); obs = [tr(f) for f in fr]
    jaw = np.array([o.jaw_open if o.ok else np.nan for o in obs]); e = env(p, n); jj = np.nan_to_num(jaw, nan=np.nanmean(jaw)); best = -9
    for lag in range(-6, 7):
        c = float(np.mean(zs(jj[max(0, lag):n + min(0, lag)]) * zs(e[max(0, -lag):n - max(0, lag)])))
        best = max(best, c)
    cen = np.array([[o.box[0] + o.box[2] / 2, o.box[1] + o.box[3] / 2] for o in obs if o.ok]); wd = np.mean([o.box[2] for o in obs if o.ok])
    sharp, sims = [], []
    for f, o in zip(fr[::5], obs[::5]):
        if not o.ok: continue
        x, y, w, h = [int(v) for v in o.box]; c = f[max(0, y):y + h, max(0, x):x + w]
        if c.size: sharp.append(cv2.Laplacian(cv2.cvtColor(cv2.resize(c, (256, 256)), cv2.COLOR_BGR2GRAY), cv2.CV_64F).var())
        q = emb(f)
        if q is not None and ref is not None: sims.append(float(rec.match(ref, q, cv2.FaceRecognizerSF_FR_COSINE)))
    r = dict(frames=n, face_found=round(float(np.mean([o.ok for o in obs])), 3), identity_cos=round(float(np.mean(sims)), 3), identity_cos_min=round(float(np.min(sims)), 3),
             jaw_range=round(float(np.nanpercentile(jaw, 95) - np.nanpercentile(jaw, 5)), 3), lip_audio_corr_weak=round(best, 3),
             head_jitter=round(float(np.mean(np.linalg.norm(np.diff(cen, axis=0), axis=1)) / wd), 4), sharpness=round(float(np.mean(sharp)), 1))
    if gt is not None:
        m = min(n, len(gt)); P, S, Pc, Sc, JD = [], [], [], [], []
        for f, g in zip(fr[:m], gt[:m]):
            a = cv2.cvtColor(f, cv2.COLOR_BGR2RGB); P.append(cv2.PSNR(a, g)); S.append(ssim(a, g, channel_axis=2))
            ac, gc = a[96:416, 96:416], g[96:416, 96:416]; Pc.append(cv2.PSNR(ac, gc)); Sc.append(ssim(ac, gc, channel_axis=2))
        r.update(psnr=round(float(np.mean(P)), 2), ssim=round(float(np.mean(S)), 3), psnr_face=round(float(np.mean(Pc)), 2), ssim_face=round(float(np.mean(Sc)), 3), paired_frames=m)
        # lip-opening agreement with the real footage (jaw trajectory corr)
        og = [tr(cv2.cvtColor(g, cv2.COLOR_RGB2BGR)) for g in gt[:m]]; jg = np.array([o.jaw_open if o.ok else np.nan for o in og]); jg = np.nan_to_num(jg, nan=np.nanmean(jg))
        r["jaw_corr_vs_real"] = round(float(np.mean(zs(jj[:m]) * zs(jg))), 3)
    res[name] = r; print(name, json.dumps(r), flush=True)
json.dump(res, open(out_path, "w"), indent=1)
