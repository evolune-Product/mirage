"""usage: render.py OUTDIR [--lora file.pt] [--mult 1.0] [--seed 42] JOB... ; JOB = name:ref.png:audio.wav  (renders OUTDIR/name.mp4)
Run with workers/.venv-flash/bin/python. Without --lora = untouched base model."""
import argparse, time, json
from common import *
import lora as L
ap = argparse.ArgumentParser(); ap.add_argument("out"); ap.add_argument("jobs", nargs="+")
ap.add_argument("--lora"); ap.add_argument("--mult", type=float, default=1.0); ap.add_argument("--seed", type=int, default=42)
ap.add_argument("--seconds", type=float, default=0, help="truncate audio (quick checks)")
a = ap.parse_args()
pipe = load_pipeline()
if a.lora:
    ck = torch.load(a.lora); L.inject(pipe.model, ck["r"], ck["alpha"]); L.load(pipe.model, ck["state"]); L.set_mult(pipe.model, a.mult)
os.makedirs(a.out, exist_ok=True)
for j in a.jobs:
    name, ref, wav = j.split(":")
    if a.seconds:
        import subprocess; t = f"/private/tmp/claude-501/-Users-revanthrajeev/6f5c2784-72c1-4d66-b8aa-e9122596874a/scratchpad/lt/cut_{name}.wav"; subprocess.run(["ffmpeg","-v","error","-y","-i",wav,"-t",str(a.seconds),t],check=True); wav = t
    t0 = time.time(); fr = render(pipe, ref, wav, a.seed); dt = time.time() - t0
    save_mp4(fr, wav, Path(a.out) / f"{name}.mp4")
    print(json.dumps(dict(job=name, frames=len(fr), sec=round(dt, 1), mps_gb=round(mem_gb(), 2))), flush=True)
