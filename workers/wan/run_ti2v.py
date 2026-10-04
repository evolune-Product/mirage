"""Wan2.2-TI2V-5B on Apple silicon (MPS). Usage: run_ti2v.py --image X --prompt "..." --out out.mp4 [--frames 33 --area 230400 --steps 20]"""
import argparse, os, sys, time, threading, resource
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "Wan2.2"))
import psutil, torch
from PIL import Image
import wan
from wan.configs import WAN_CONFIGS
from wan.utils.utils import save_video

ap = argparse.ArgumentParser()
ap.add_argument("--image", required=True); ap.add_argument("--prompt", required=True); ap.add_argument("--out", required=True)
ap.add_argument("--ckpt", default=os.path.join(HERE, "ckpt")); ap.add_argument("--frames", type=int, default=33)
ap.add_argument("--area", type=int, default=480 * 480); ap.add_argument("--steps", type=int, default=20)
ap.add_argument("--guide", type=float, default=5.0); ap.add_argument("--seed", type=int, default=42)
ap.add_argument("--t5", default="umt5_fp8.safetensors")
a = ap.parse_args()

peak = {"rss": 0, "mps": 0}
def mon():
    p = psutil.Process()
    while True:
        peak["rss"] = max(peak["rss"], p.memory_info().rss)
        try: peak["mps"] = max(peak["mps"], torch.mps.driver_allocated_memory())
        except Exception: pass
        time.sleep(0.5)
threading.Thread(target=mon, daemon=True).start()

# lazy + cached T5 (CPU, ~3.5 min to load fp8->bf16, ~45 s per prompt): contexts are cached on disk by prompt
import hashlib, wan.textimage2video as _ti, wan.modules.t5 as _t5
class CachedT5:
    def __init__(self, **kw): self.kw = kw; self.real = None; self.model = torch.nn.Identity()
    def __call__(self, texts, device):
        f = os.path.join(HERE, "ckpt", "ctx_" + hashlib.md5(texts[0].encode()).hexdigest() + ".pt")
        if os.path.exists(f): return [t.to(device) for t in torch.load(f)]
        if self.real is None: self.real = _t5.T5EncoderModel(**self.kw)
        out = [t.cpu() for t in self.real(texts, torch.device("cpu"))]
        torch.save(out, f); return [t.to(device) for t in out]
_ti.T5EncoderModel = CachedT5

cfg = WAN_CONFIGS["ti2v-5B"]
cfg.t5_checkpoint = a.t5
t0 = time.time()
m = wan.WanTI2V(config=cfg, checkpoint_dir=a.ckpt, device_id=0, rank=0, t5_cpu=True, convert_model_dtype=True)
t1 = time.time(); print(f"[load] {t1-t0:.1f}s rss={peak['rss']/1e9:.1f}GB", flush=True)
img = Image.open(a.image).convert("RGB")
vid = m.generate(a.prompt, img=img, max_area=a.area, frame_num=a.frames, shift=cfg.sample_shift, sample_solver="unipc",
                 sampling_steps=a.steps, guide_scale=a.guide, seed=a.seed, offload_model=False)
t2 = time.time()
print(f"[gen] {t2-t1:.1f}s  video {tuple(vid.shape)}  peak_rss={peak['rss']/1e9:.1f}GB peak_mps_alloc={peak['mps']/1e9:.1f}GB "
      f"maxrss={resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1e9:.1f}GB", flush=True)
torch.save(vid.cpu(), a.out + ".pt")
save_video(tensor=vid[None], save_file=a.out, fps=cfg.sample_fps, nrow=1, normalize=True, value_range=(-1, 1))
print("saved", a.out)
