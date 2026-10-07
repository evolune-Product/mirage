"""Render a talking clip with SoulX-FlashHead (generative whole-face + head motion from audio): face image + speech wav -> mp4.

Same CLI and JSON result as render_liveportrait.py so the job worker can swap renderers.
usage: render_flashhead.py --image face.png --audio speech.wav --out out.mp4 [--fps 25] [--model lite|pro]
Runs the checkout in workers/SoulX-FlashHead with workers/.venv-flash (Mac MPS patch: workers/patches/). Needs an NVIDIA GPU for
real time; on Apple silicon Lite is ~19x slower than real time (measured, M1 Pro). Weights/licence: docs/LICENSES.md (Apache-2.0 per README)."""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
FH = Path(os.environ.get("VOCALFACE_FLASHHEAD_DIR", HERE / "SoulX-FlashHead"))
PY = Path(os.environ.get("VOCALFACE_FLASHHEAD_PY", HERE / ".venv-flash" / "bin" / "python"))


def available() -> tuple[bool, str]:
    need = [FH / "generate_video.py", PY, FH / "models/SoulX-FlashHead-1_3B/Model_Lite/diffusion_pytorch_model.safetensors",
            FH / "models/wav2vec2-base-960h/pytorch_model.bin"]
    miss = [str(p) for p in need if not p.exists()]
    return (not miss, "missing: " + ", ".join(miss) if miss else "")


def tight_crop(image: Path, out_png: Path, size: int = 768) -> dict:
    """Tight face crop via face_crop.py in the .venv-face interpreter (MediaPipe needs that environment). Falls back to the
    untouched image on any failure."""
    py = HERE / ".venv-face" / "bin" / "python"
    if not py.exists():
        return {"cropped": False, "why": ".venv-face missing"}
    r = subprocess.run([str(py), str(HERE / "face_crop.py"), str(image), str(out_png), str(size)], capture_output=True, text=True)
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:  # noqa: BLE001
        return {"cropped": False, "why": (r.stderr or r.stdout)[-200:]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True)
    ap.add_argument("--audio", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--fps", type=float, default=25.0)  # FlashHead is fixed 25 fps; accepted for CLI compatibility
    ap.add_argument("--model", default=os.environ.get("VOCALFACE_FLASHHEAD_MODEL", "lite"), choices=["lite", "pro"])
    ap.add_argument("--no-face-crop", action="store_true")
    a = ap.parse_args()
    ok, why = available()
    if not ok:
        print(json.dumps({"ok": False, "error": f"flashhead unavailable ({why})"}))
        return 1
    t0 = time.time()
    with tempfile.TemporaryDirectory() as td:
        wav16 = Path(td) / "a16.wav"
        r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", a.audio, "-ar", "16000", "-ac", "1", str(wav16)],
                           capture_output=True, text=True)
        if r.returncode:
            print(json.dumps({"ok": False, "error": "audio convert failed: " + r.stderr[-300:]}))
            return 1
        cond = Path(a.image).resolve()
        if not a.no_face_crop:
            try:
                info = tight_crop(cond, Path(td) / "cond.png")
                if info.get("cropped"):
                    cond = Path(td) / "cond.png"
            except Exception:  # noqa: BLE001 - any crop failure falls back to the original image
                pass
        cmd = [str(PY), "generate_video.py", "--ckpt_dir", "models/SoulX-FlashHead-1_3B", "--wav2vec_dir", "models/wav2vec2-base-960h",
               "--model_type", a.model, "--cond_image", str(cond), "--audio_path", str(wav16),
               "--audio_encode_mode", "stream", "--save_file", str(Path(a.out).resolve())]
        env = {**os.environ, "PYTORCH_ENABLE_MPS_FALLBACK": "1"}
        r = subprocess.run(cmd, cwd=FH, env=env, capture_output=True, text=True)
    if r.returncode or not Path(a.out).exists():
        print(json.dumps({"ok": False, "error": (r.stderr or r.stdout)[-800:]}))
        return 1
    print(json.dumps({"ok": True, "seconds": round(time.time() - t0, 1), "engine": f"flashhead-{a.model}"}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
