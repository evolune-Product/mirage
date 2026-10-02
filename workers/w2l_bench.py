"""Wav2Lip benchmark on Apple GPU (MPS): lip-sync a real base video to speech audio.
Base-video frames give natural head motion and blinking; Wav2Lip only replaces the mouth region.
usage: w2l_bench.py BASE_VIDEO AUDIO16K.wav OUT.mp4 [device] [seconds]"""
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import torch

W2L = Path(__file__).resolve().parent / "Wav2Lip"
sys.path.insert(0, str(W2L))
import audio as w2l_audio  # noqa: E402
from models import Wav2Lip  # noqa: E402


def load(device: str, ckpt: str = "wav2lip_gan.pth"):
    m = Wav2Lip()
    sd = torch.load(W2L / "checkpoints" / ckpt, map_location="cpu", weights_only=False)["state_dict"]
    m.load_state_dict({k.replace("module.", ""): v for k, v in sd.items()})
    return m.to(device).eval()


def main(video, wav, out, device="mps", seconds=7.0):
    t_all = time.time()
    model = load(device)
    cap = cv2.VideoCapture(video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    n_frames = int(seconds * fps)
    frames = []
    while len(frames) < n_frames:
        ok, f = cap.read()
        if not ok:
            break
        frames.append(f)
    # loop base video (forward then reversed = seamless "ping-pong" idle) if shorter than audio
    base = frames + frames[::-1]
    # --- audio -> mel chunks aligned to video frames
    wavdata = w2l_audio.load_wav(wav, 16000)
    mel = w2l_audio.melspectrogram(wavdata)
    mel_step, mel_idx_mult = 16, 80.0 / fps
    n_out = int(len(wavdata) / 16000 * fps)
    mel_chunks = []
    for i in range(n_out):
        s = int(i * mel_idx_mult)
        if s + mel_step > mel.shape[1]:
            mel_chunks.append(mel[:, -mel_step:])
        else:
            mel_chunks.append(mel[:, s:s + mel_step])
    # --- face box from first frame (Haar), padded; held fixed (base clip head motion is small)
    det = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    g = cv2.cvtColor(base[0], cv2.COLOR_BGR2GRAY)
    faces = det.detectMultiScale(g, 1.1, 6, minSize=(80, 80))
    x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
    pad = int(0.18 * h)
    y1, y2, x1, x2 = max(0, y - pad // 4), min(base[0].shape[0], y + h + pad), max(0, x - pad // 2), min(base[0].shape[1], x + w + pad // 2)
    print(f"face box {x2-x1}x{y2-y1}, fps {fps}, out frames {n_out}")
    # --- batched inference
    bs, outs = 32, []
    infer_t = 0.0
    for b0 in range(0, n_out, bs):
        idx = range(b0, min(b0 + bs, n_out))
        imgs, mels = [], []
        for i in idx:
            fr = base[i % len(base)]
            crop = cv2.resize(fr[y1:y2, x1:x2], (96, 96))
            imgs.append(crop); mels.append(mel_chunks[i][:, :, None] if mel_chunks[i].ndim == 2 else mel_chunks[i])
        img = np.asarray(imgs)
        masked = img.copy(); masked[:, 48:] = 0
        inp = np.concatenate((masked, img), axis=3) / 255.0
        mel_b = np.asarray(mels).reshape(len(imgs), 80, 16, 1)
        it = torch.FloatTensor(np.transpose(inp, (0, 3, 1, 2))).to(device)
        mt = torch.FloatTensor(np.transpose(mel_b, (0, 3, 1, 2))).to(device)
        if device == "mps":
            torch.mps.synchronize()
        t0 = time.time()
        with torch.no_grad():
            pred = model(mt, it)
        if device == "mps":
            torch.mps.synchronize()
        infer_t += time.time() - t0
        outs.extend((pred.cpu().numpy().transpose(0, 2, 3, 1) * 255.0).astype(np.uint8))
    print(f"model inference: {infer_t:.2f}s for {n_out} frames => {n_out/infer_t:.1f} fps (needs {fps:.0f} for real time)")
    # --- paste mouth region back with feathered mask
    H, Wd = base[0].shape[:2]
    vw = cv2.VideoWriter("/tmp/_w2l_noaudio.mp4", cv2.VideoWriter_fourcc(*"mp4v"), fps, (Wd, H))
    ch, cw = y2 - y1, x2 - x1
    # oval mask around mouth/chin instead of a rectangle: no hard seams
    yy, xx = np.mgrid[0:ch, 0:cw].astype(np.float32)
    cy, cx = ch * 0.74, cw * 0.5
    mask = np.clip(1.0 - (((xx - cx) / (cw * 0.40)) ** 2 + ((yy - cy) / (ch * 0.30)) ** 2), 0, 1)
    mask = cv2.GaussianBlur(mask, (0, 0), sigmaX=cw * 0.03)[:, :, None] ** 0.6
    t0 = time.time()
    for i in range(n_out):
        fr = base[i % len(base)].copy()
        gen = cv2.resize(outs[i], (cw, ch), interpolation=cv2.INTER_LANCZOS4).astype(np.float32)
        reg = fr[y1:y2, x1:x2].astype(np.float32)
        # colour-match generated patch to the original patch (per-channel mean/std over the mouth zone)
        zy = slice(int(ch * 0.55), ch); gm, gs = gen[zy].mean((0, 1)), gen[zy].std((0, 1)) + 1e-3
        rm, rs = reg[zy].mean((0, 1)), reg[zy].std((0, 1)) + 1e-3
        gen = np.clip((gen - gm) / gs * rs + rm, 0, 255)
        # unsharp mask to recover some of the detail lost by the 96x96 model resolution
        blur = cv2.GaussianBlur(gen, (0, 0), 1.4); gen = np.clip(gen + 0.9 * (gen - blur), 0, 255)
        fr[y1:y2, x1:x2] = (gen * mask + reg * (1 - mask)).astype(np.uint8)
        vw.write(fr)
    vw.release()
    print(f"composite {time.time()-t0:.2f}s; total {time.time()-t_all:.1f}s")
    import subprocess
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", "/tmp/_w2l_noaudio.mp4", "-i", wav, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", out], check=True)
    print("wrote", out)


if __name__ == "__main__":
    a = sys.argv
    main(a[1], a[2], a[3], a[4] if len(a) > 4 else "mps", float(a[5]) if len(a) > 5 else 7.0)
