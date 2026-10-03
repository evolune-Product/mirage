"""Voice-cloning sidecar: Chatterbox (MIT) on Apple MLX, stdin/stdout, run with the `.venv-clone` python (mlx-audio).

It has NO dependency on the `app` package. Protocol (binary, little endian), same framing as pipeline/mlx_tts_worker.py:
  request  (stdin):  one JSON line per call
       {"op": "tts",    "id": n, "text": str, "ref": "/path/ref.wav", "lang": "en"}
       {"op": "prep",   "id": n, "ref": "/path/ref.wav"}       # pre-compute the speaker conditioning (no audio)
       {"op": "cancel", "id": n}
  response (stdout): frames `<III>` = (id, kind, nbytes) + payload.
       kind 0 = int16 24 kHz PCM chunk, 1 = end of request, 2 = error (utf-8), 3 = ready (once, after the model loaded)
Requests are served one at a time (one model); a cancelled queued request is skipped."""
import json
import os
import queue
import struct
import sys
import threading

_out = os.fdopen(os.dup(1), "wb", buffering=0)
os.dup2(2, 1)
sys.stdout = sys.stderr

import numpy as np  # noqa: E402

REPO = os.environ.get("MIRAGE_CLONE_REPO", "mlx-community/chatterbox-4bit")
EXAGGERATION = float(os.environ.get("MIRAGE_CLONE_EXAGGERATION", "0.3"))
CFG = float(os.environ.get("MIRAGE_CLONE_CFG", "0.5"))
TEMP = float(os.environ.get("MIRAGE_CLONE_TEMPERATURE", "0.8"))


def send(rid: int, kind: int, payload: bytes = b"") -> None:
    _out.write(struct.pack("<III", rid, kind, len(payload)) + payload)


def trim(a: np.ndarray, thresh: float = 0.004, pad: int = 480) -> np.ndarray:
    idx = np.flatnonzero(np.abs(a) > thresh)
    return a if len(idx) == 0 else a[max(0, idx[0] - pad): idx[-1] + 1 + pad]


def main() -> None:
    import mlx.core as mx
    from mlx_audio.tts.utils import load_model

    model = load_model(REPO)
    conds_cache: dict[tuple, object] = {}

    def conds_for(ref: str):
        key = (ref, os.path.getmtime(ref))
        c = conds_cache.get(key)
        if c is None:
            if len(conds_cache) >= 8:
                conds_cache.pop(next(iter(conds_cache)))
            from mlx_audio.utils import load_audio

            wav = load_audio(ref, sample_rate=model.sample_rate)
            c = model.prepare_conditionals(wav, model.sample_rate, EXAGGERATION)
            mx.eval(*[v for v in (c.t3.speaker_emb, c.t3.cond_prompt_speech_tokens) if v is not None])
            conds_cache[key] = c
        return c

    reqs: queue.Queue = queue.Queue()
    cancelled: set[int] = set()

    def reader() -> None:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if r.get("op") == "cancel":
                cancelled.add(r["id"])
            else:
                reqs.put(r)
        reqs.put(None)

    threading.Thread(target=reader, daemon=True).start()
    send(0, 3)
    while True:
        req = reqs.get()
        if req is None:
            return
        rid = req["id"]
        if rid in cancelled:
            cancelled.discard(rid)
            send(rid, 1)
            continue
        try:
            c = conds_for(req["ref"])
            if req.get("op") == "tts":
                for r in model.generate(text=req["text"], conds=c, exaggeration=EXAGGERATION, cfg_weight=CFG,
                                        temperature=TEMP, lang_code=req.get("lang") or "en", verbose=False):
                    a = trim(np.asarray(r.audio, dtype=np.float32).reshape(-1))
                    if rid in cancelled:
                        break
                    send(rid, 0, (np.clip(a, -1, 1) * 32767).astype(np.int16).tobytes())
        except Exception as e:  # noqa: BLE001
            send(rid, 2, f"{type(e).__name__}: {e}".encode()[:500])
        cancelled.discard(rid)
        send(rid, 1)


if __name__ == "__main__":
    main()
