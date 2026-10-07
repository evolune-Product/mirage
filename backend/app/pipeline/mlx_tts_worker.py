"""Kokoro-82M on Apple's MLX (Metal GPU) as a stdin/stdout sidecar. Run with a python that has `mlx-audio` + `misaki[en]`:

    <venv>/bin/python mlx_tts_worker.py

Why a sidecar: the main backend runs on the system Python 3.14, where mlx-audio's dependency chain (spacy, torch) does not
install. This process has no dependency on the `app` package, so it can live in any venv.

Protocol (binary, little endian):
  request  (stdin):   one JSON line per call: {"id": int, "text": str, "voice": str}
  response (stdout):  frames `<III>` = (id, kind, nbytes) + payload.   kind 0 = int16 24 kHz PCM chunk, 1 = end of request,
                      2 = error (utf-8 message), 3 = ready (sent once after warm-up).
Cancel: a JSON line {"cancel": id} abandons that request (queued or in flight) - barge-in. Requests never abandon each other:
the sidecar is shared by several conversations (a newer request used to cut the older one off, truncating its audio)."""
import json
import os
import queue
import struct
import sys
import threading

# Keep the real stdout for the protocol; anything the libraries print goes to stderr.
_out = os.fdopen(os.dup(1), "wb", buffering=0)
os.dup2(2, 1)
sys.stdout = sys.stderr

import numpy as np  # noqa: E402

REPO = os.environ.get("VOCALFACE_MLX_TTS_REPO", "mlx-community/Kokoro-82M-bf16")
DEFAULT_VOICE = "af_heart"


def send(rid: int, kind: int, payload: bytes = b"") -> None:
    _out.write(struct.pack("<III", rid, kind, len(payload)) + payload)


GAIN = float(os.environ.get("VOCALFACE_MLX_TTS_GAIN", "1.35"))  # MLX bf16 output is ~3 dB quieter than the ONNX build


def trim(a: np.ndarray, thresh: float = 0.004, pad: int = 480) -> np.ndarray:
    idx = np.flatnonzero(np.abs(a) > thresh)
    return a if len(idx) == 0 else a[max(0, idx[0] - pad): idx[-1] + 1 + pad]


def main() -> None:
    from mlx_audio.tts.utils import load_model

    model = load_model(REPO)
    voices_ok: dict[str, bool] = {}

    def synth(text: str, voice: str):
        for r in model.generate(text=text, voice=voice, speed=1.0, lang_code="a"):
            yield np.asarray(r.audio, dtype=np.float32).reshape(-1)

    for t in ("Hello there.", "Sure, the starter plan costs nineteen dollars a month."):  # warm-up / kernel compile
        list(synth(t, DEFAULT_VOICE))

    reqs: queue.Queue = queue.Queue()
    cancelled: set = set()

    def reader() -> None:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            m = json.loads(line)
            if "cancel" in m:
                cancelled.add(m["cancel"])
            else:
                reqs.put(m)
        reqs.put(None)

    threading.Thread(target=reader, daemon=True).start()
    send(0, 3)
    while True:
        req = reqs.get()
        if req is None:
            return
        rid = req["id"]
        if rid in cancelled:  # cancelled while still queued
            cancelled.discard(rid)
            send(rid, 1)
            continue
        voice = req.get("voice") or DEFAULT_VOICE
        try:
            try:
                gen = synth(req["text"], voice)
                first = next(gen, None)
            except Exception:
                if voice == DEFAULT_VOICE:
                    raise
                gen = synth(req["text"], DEFAULT_VOICE)  # unknown voice: fall back rather than fail the turn
                first = next(gen, None)
            chunks = ([first] if first is not None else [])
            while True:
                for a in chunks:
                    a = trim(a)
                    send(rid, 0, (np.clip(a * GAIN, -1, 1) * 32767).astype(np.int16).tobytes())
                if rid in cancelled:  # the client barged in / went away: drop the rest of this request
                    break
                nxt = next(gen, None)
                if nxt is None:
                    break
                chunks = [nxt]
            cancelled.discard(rid)
        except Exception as e:  # noqa: BLE001
            send(rid, 2, f"{type(e).__name__}: {e}".encode()[:500])
        send(rid, 1)


if __name__ == "__main__":
    main()
