"""Kokoro speech for the fake microphone: python tts.py "text" voice out.wav  (prints duration in seconds).
Run with the backend venv. Leading/trailing silence so VAD endpointing and the recorder have clean edges."""
import sys
from pathlib import Path

import numpy as np
import soundfile as sf
from kokoro_onnx import Kokoro

ROOT = Path(__file__).resolve().parents[1]
text, voice, out = sys.argv[1], sys.argv[2], sys.argv[3]
speed = float(sys.argv[4]) if len(sys.argv) > 4 else 1.0
tail = float(sys.argv[5]) if len(sys.argv) > 5 else 1.2  # chromium loops the file: a long silent tail = the question is asked once
k = Kokoro(str(ROOT / "models/kokoro-v1.0.onnx"), str(ROOT / "models/voices-v1.0.bin"))
a, sr = k.create(text, voice=voice, speed=speed)
a = np.concatenate([np.zeros(int(sr * 0.8), dtype=np.float32), a, np.zeros(int(sr * tail), dtype=np.float32)])
sf.write(out, a, sr)
print(len(a) / sr)
