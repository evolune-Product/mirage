"""Offline talking-head render with the full face pipeline (tracking, overlay crop, calm base, improved compositing).

  .venv-face/bin/python render_offline.py --source VIDEO --audio SPEECH.wav --out OUT.mp4
        [--engine wav2lip|musetalk] [--listening CLIP.mp4] [--restore none|sr] [--seconds N] [--workdir DIR]

The replica dir is created under --workdir (default /tmp/mirage_offline/<name>) so nothing in the product data is touched."""
import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import face_render as fr  # noqa: E402
import facelib as fl  # noqa: E402


def load_audio16(path: str, seconds: float | None):
    cmd = ["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", "16000", "-f", "f32le", "-"]
    if seconds:
        cmd[3:3] = ["-t", str(seconds)]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32).copy()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--listening")
    ap.add_argument("--audio", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--engine", default="wav2lip", choices=["wav2lip", "musetalk", "viseme"])
    ap.add_argument("--restore", default="none")
    ap.add_argument("--seconds", type=float)
    ap.add_argument("--workdir", default="/tmp/mirage_offline")
    ap.add_argument("--no-track", action="store_true")
    ap.add_argument("--sharpen", type=float, default=0.6)
    ap.add_argument("--seamless", action="store_true")
    ap.add_argument("--frames-dir")
    a = ap.parse_args()
    from engines import CommercialOnlyError, check_allowed
    try:
        check_allowed(a.engine)
    except CommercialOnlyError as e:
        raise SystemExit(f'refused: {e}')

    rdir = Path(a.workdir) / Path(a.source).stem
    rdir.mkdir(parents=True, exist_ok=True)
    if not (rdir / "source.mp4").exists() or (rdir / "source.mp4").stat().st_size != Path(a.source).stat().st_size:
        shutil.copyfile(a.source, rdir / "source.mp4")
    if a.listening:
        shutil.copyfile(a.listening, rdir / "listening.mp4")
    device = fr.pick_device()
    tracker = None if a.no_track else fl.FaceTracker()
    t0 = time.time()
    base = fr.prepare_base(rdir, tracker)
    print(f"base: {len(base.frames)} frames crop={base.info['crop']} overlays={base.info['overlays']} "
          f"jaw_mean={base.info['jaw_mean']:.3f} (global {base.info['jaw_global_mean']:.3f}) prep {time.time()-t0:.1f}s", flush=True)
    a16 = load_audio16(a.audio, a.seconds)
    n = int(len(a16) / 16000 * fr.FPS)
    seq = fr.pingpong_seq(base, n)
    t0 = time.time()
    if a.engine == "viseme":
        import engines
        eng = engines.create("viseme", device)
        outs = eng.generate(base, seq, eng.mel_chunks(a16))
        a.sharpen = 0.0
    elif a.engine == "wav2lip":
        eng = fr.Wav2LipEngine(device)
        outs = eng.generate(base, seq, eng.mel_chunks(a16))
    else:
        from musetalk_engine import MuseTalkEngine, musetalk_box
        eng = MuseTalkEngine(device)
        H, W = base.frames[0].shape[:2]
        # MuseTalk uses its own crop convention (nose-bridge symmetric, +10 px below chin)
        boxes = np.array([musetalk_box(p, H, W) if p is not None else base.boxes[i] for i, p in enumerate(base.pts)], np.int32)
        base.boxes = boxes
        crops = [cv2.resize(f[b[1]:b[3], b[0]:b[2]], (256, 256), interpolation=cv2.INTER_LANCZOS4) for f, b in zip(base.frames, boxes)]
        t1 = time.time()
        lat = eng.encode(crops)
        print(f"vae encode {len(crops)} base frames {time.time()-t1:.1f}s")
        prompts = eng.audio_chunks(a16)
        outs = eng.generate(lat, prompts, seq)
        eng.size = 256
    fr.sync(device)
    dt = time.time() - t0
    print(f"{a.engine}: {n} frames in {dt:.2f}s = {n/dt:.1f} fps (incl. audio features), device={device}", flush=True)
    if a.restore != "none":
        from restore import restore_faces
        t1 = time.time()
        outs = restore_faces(outs, a.restore, device)
        print(f"restore[{a.restore}] {time.time()-t1:.1f}s")
    t0 = time.time()
    sa = fr.speech_alpha(a16, n)
    frames = [fr.paste(base, seq[i], outs[i], sharpen=a.sharpen, seamless=a.seamless, alpha=float(sa[i])) for i in range(n)]
    print(f"composite {time.time()-t0:.2f}s ({(time.time()-t0)/n*1000:.1f} ms/frame)")
    if a.frames_dir:
        Path(a.frames_dir).mkdir(parents=True, exist_ok=True)
        for i in range(0, n, 6):
            cv2.imwrite(f"{a.frames_dir}/{i:04d}.png", frames[i])
    tmp = Path(a.workdir) / "_noaudio.mp4"
    h, w = frames[0].shape[:2]
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}", "-r", str(fr.FPS),
                          "-i", "-", "-i", a.audio] + (["-t", str(a.seconds)] if a.seconds else []) +
                         ["-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", "-c:a", "aac",
                          "-shortest", a.out], stdin=subprocess.PIPE)
    for f in frames:
        p.stdin.write(f.tobytes())
    p.stdin.close(); p.wait()
    print("wrote", a.out)


if __name__ == "__main__":
    main()
