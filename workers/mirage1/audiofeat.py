"""Audio -> per-video-frame features at 25 fps using facebook/wav2vec2-base-960h (Apache-2.0), an audio-only pretrained encoder
that is NOT trained on any talking-face data. Layer 9 hidden state (50 Hz) averaged in pairs -> 25 Hz x 768."""
import os
import subprocess
from pathlib import Path

import numpy as np
import torch

HERE = Path(__file__).resolve().parent
W2V = Path(os.environ.get("MIRAGE1_W2V", HERE.parent / "SoulX-FlashHead/models/wav2vec2-base-960h"))  # weights = facebook/wav2vec2-base-960h
LAYER = 9
_model = None


def read_wav16k(path) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32).copy()


@torch.no_grad()
def features(wav: np.ndarray, fps: int = 25, device: str = "cpu") -> np.ndarray:
    global _model
    from transformers import Wav2Vec2Model
    if _model is None:
        _model = Wav2Vec2Model.from_pretrained(str(W2V)).eval()
    m = _model.to(device)
    x = (wav - wav.mean()) / (wav.std() + 1e-7)
    chunks, hop, win = [], 16000 * 20, 16000 * 20  # whole clip at once is fine for <=20 s; chunk longer audio
    outs = []
    for s in range(0, len(x), hop):
        seg = torch.from_numpy(x[s:s + win])[None].to(device)
        if seg.shape[1] < 400:
            break
        hs = m(seg, output_hidden_states=True).hidden_states[LAYER][0].cpu().numpy()
        outs.append(hs)
    h = np.concatenate(outs, 0)  # 50 Hz
    assert fps == 25
    n = len(h) // 2
    return h[: n * 2].reshape(n, 2, -1).mean(1).astype(np.float32)
