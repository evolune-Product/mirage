import os, sys, time
from pathlib import Path
FH = Path(__file__).resolve().parents[1] / "SoulX-FlashHead"
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
os.chdir(FH); sys.path.insert(0, str(FH)); sys.path.insert(0, str(Path(__file__).parent))
import mac_compat  # noqa
import numpy as np, torch
from flash_head.inference import get_pipeline, get_base_data, get_infer_params, get_audio_embedding, run_pipeline
DATA = FH / "lora/data"

def load_pipeline():
    return get_pipeline(world_size=1, ckpt_dir="models/SoulX-FlashHead-1_3B", wav2vec_dir="models/wav2vec2-base-960h", model_type="lite")

def mem_gb():
    return torch.mps.driver_allocated_memory() / 2**30

def render(pipe, ref_png, wav_path, seed=42):
    """same chunking as generate_video.py 'stream' mode; returns uint8 frames (N,H,W,3)"""
    import librosa
    from collections import deque
    get_base_data(pipe, str(ref_png), seed, False)
    ip = get_infer_params(); sr, fps, dur = ip["sample_rate"], ip["tgt_fps"], ip["cached_audio_duration"]
    fn, mf = ip["frame_num"], ip["motion_frames_num"]; sl = fn - mf
    a, _ = librosa.load(str(wav_path), sr=sr, mono=True)
    n = sl * sr // fps
    r = len(a) % n
    if r: a = np.concatenate([a, np.zeros(n - r, a.dtype)])
    L = sr * dur; dq = deque([0.0] * L, maxlen=L); end = dur * fps; start = end - fn
    out = []
    for chunk in a.reshape(-1, n):
        dq.extend(chunk.tolist())
        emb = get_audio_embedding(pipe, np.array(dq), start, end)
        out.append(run_pipeline(pipe, emb)[mf:].cpu())
    return torch.cat(out).numpy().astype(np.uint8)

def save_mp4(frames, wav, path, fps=25):
    import imageio, subprocess
    tmp = str(path).replace(".mp4", "_tmp.mp4")
    with imageio.get_writer(tmp, format="mp4", mode="I", fps=fps, codec="h264", ffmpeg_params=["-bf", "0"]) as w:
        for f in frames: w.append_data(f)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", tmp, "-i", str(wav), "-c:v", "copy", "-c:a", "aac", "-shortest", str(path)], check=True)
    os.remove(tmp)
