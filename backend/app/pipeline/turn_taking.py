"""Turn-taking: decides when the user has started/finished speaking and when to interrupt the agent (barge-in).

Two voice-activity detectors behind one tiny interface (`prob(int16 PCM chunk) -> speech probability 0..1`):

* `SileroVAD`  - Silero VAD v5/6 (ONNX, ~2 MB, runs in onnxruntime in well under 1 ms per 32 ms chunk). Robust to
                 keyboard clicks, fans, music and room noise that fool a plain loudness gate.
* `EnergyVAD`  - RMS gate. Zero dependencies; the automatic fallback if the model or onnxruntime is unavailable
                 (and what unit tests with synthetic square waves use).

`TurnTaker.push()` keeps the original contract (returns 'speech' | 'silence' | 'end_of_turn') and adds two things
for low-latency endpointing:

* 'pause' is returned once, when the silence after speech first reaches `pause_ms`. The session uses it to start
  transcribing speculatively, so STT runs *inside* the endpointing window instead of after it.
* `force_end()` lets the session end the turn early (e.g. the speculative transcript is a complete sentence).
"""
from __future__ import annotations

import array
import os
import threading
from dataclasses import dataclass
from pathlib import Path

ASSET = Path(__file__).resolve().parent / "assets" / "silero_vad.onnx"
CHUNK = 512  # Silero window at 16 kHz = 32 ms
CONTEXT = 64


class EnergyVAD:
    """Loudness gate: speech if RMS > threshold. `prob` is 1.0/0.0 so it fits the same interface."""

    chunk_samples = 0  # accepts any size: caller passes whole frames

    def __init__(self, threshold: float = 500.0):
        self.threshold = threshold

    @staticmethod
    def rms(frame: bytes) -> float:
        a = array.array("h", frame[: len(frame) // 2 * 2])
        return (sum(x * x for x in a) / max(len(a), 1)) ** 0.5

    def prob(self, frame: bytes) -> float:
        return 1.0 if self.rms(frame) > self.threshold else 0.0

    def reset(self) -> None:
        pass


_ort_session = None
_ort_lock = threading.Lock()


def _silero_session():
    global _ort_session
    with _ort_lock:
        if _ort_session is None:
            import onnxruntime as ort

            so = ort.SessionOptions()
            so.intra_op_num_threads = 1  # tiny model: a thread pool only adds overhead / contention
            so.inter_op_num_threads = 1
            so.log_severity_level = 3
            _ort_session = ort.InferenceSession(str(ASSET), so, providers=["CPUExecutionProvider"])
        return _ort_session


def silero_available() -> bool:
    try:
        _silero_session()
        return True
    except Exception:
        return False


class SileroVAD:
    """Streaming Silero VAD. One instance per audio stream (it carries LSTM state); the ONNX session is shared."""

    chunk_samples = CHUNK

    def __init__(self):
        import numpy as np

        self.np = np
        self.sess = _silero_session()
        self.reset()

    def reset(self) -> None:
        np = self.np
        self.state = np.zeros((2, 1, 128), dtype=np.float32)
        self.ctx = np.zeros((1, CONTEXT), dtype=np.float32)
        self.sr = np.array(16000, dtype=np.int64)

    def prob(self, chunk: bytes) -> float:
        np = self.np
        x = np.frombuffer(chunk, dtype=np.int16).astype(np.float32)[None, :] / 32768.0
        inp = np.concatenate([self.ctx, x], axis=1)
        out, self.state = self.sess.run(None, {"input": inp, "state": self.state, "sr": self.sr})
        self.ctx = inp[:, -CONTEXT:]
        return float(out[0][0])


def make_vad(kind: str | None = None, energy_threshold: float = 500.0):
    """kind: 'silero' | 'energy' | 'auto' (default: env MIRAGE_VAD or auto = silero, falling back to energy)."""
    kind = (kind or os.environ.get("MIRAGE_VAD") or "auto").lower()
    if kind in ("silero", "auto"):
        try:
            return SileroVAD()
        except Exception:
            if kind == "silero":
                raise
    return EnergyVAD(energy_threshold)


@dataclass
class TurnState:
    speaking: bool = False
    silence_ms: int = 0
    end_of_turn_ms: int = 600
    pause_ms: int = 0
    paused: bool = False
    speech_prob: float = 0.0


class TurnTaker:
    def __init__(self, end_of_turn_ms: int = 600, energy_threshold: float = 500.0, vad=None,
                 pause_ms: int = 0, on_threshold: float = 0.5, off_threshold: float = 0.35):
        """end_of_turn_ms: silence that always ends the turn. pause_ms (0 = off): silence at which 'pause' fires."""
        self.s = TurnState(end_of_turn_ms=end_of_turn_ms, pause_ms=pause_ms)
        self.threshold = energy_threshold
        self.vad = vad if vad is not None else EnergyVAD(energy_threshold)
        self.on, self.off = on_threshold, off_threshold
        self.suppress = False  # session sets this while a mic frame is explained by agent echo: never counts as speech
        self.min_rms = 0.0  # when > 0, speech must also be at least this loud (session raises it while the agent talks)
        self._buf = b""
        self._in_speech = False  # VAD hysteresis state (distinct from s.speaking, which spans short pauses)
        self.speech_prob = 0.0

    @staticmethod
    def energy(frame: bytes) -> float:
        return EnergyVAD.rms(frame)

    def _is_speech(self, frame: bytes) -> bool:
        """Feeds the VAD (re-chunking for fixed-window models) and returns the hysteresis-filtered decision."""
        n = self.vad.chunk_samples * 2
        if not n:  # energy VAD: whole frame
            self.speech_prob = self.vad.prob(frame)
            if self.suppress or (self.min_rms and EnergyVAD.rms(frame) < self.min_rms):
                self.speech_prob = 0.0
            self._in_speech = self.speech_prob >= 0.5
            return self._in_speech
        self._buf += frame
        while len(self._buf) >= n:
            chunk, self._buf = self._buf[:n], self._buf[n:]
            p = self.vad.prob(chunk)
            if self.suppress or (self.min_rms and EnergyVAD.rms(chunk) < self.min_rms):
                p = 0.0  # too quiet to be the user over the agent's own (echoed) voice
            self.speech_prob = p
            self._in_speech = p >= (self.off if self._in_speech else self.on)
        return self._in_speech

    def push(self, frame: bytes, frame_ms: int = 20) -> str:
        """Returns 'speech', 'silence', 'pause' (first time silence reaches pause_ms) or 'end_of_turn'."""
        s = self.s
        s.speech_prob = self.speech_prob
        if self._is_speech(frame):
            s.speaking, s.silence_ms, s.paused = True, 0, False
            return "speech"
        if not s.speaking:
            return "silence"
        s.silence_ms += frame_ms
        if s.silence_ms >= s.end_of_turn_ms:
            return self.force_end()
        if s.pause_ms and not s.paused and s.silence_ms >= s.pause_ms:
            s.paused = True
            return "pause"
        return "silence"

    def force_end(self) -> str:
        """End the current turn now (caller decided the user is done)."""
        s = self.s
        s.speaking, s.silence_ms, s.paused = False, 0, False
        return "end_of_turn"


# Words that almost never end a finished thought: if the transcript ends with one, keep waiting for the user.
_DANGLING = {"and", "but", "or", "so", "because", "the", "a", "an", "of", "to", "in", "on", "for", "with", "that", "if",
             "um", "uh", "like", "my", "your", "is", "are", "was", "i", "we", "you", "about", "then", "also", "as", "at"}


def utterance_complete(text: str) -> bool:
    """True when a (speculative) transcript reads like a finished sentence: ends in . ? ! (not an ellipsis) and not on a
    dangling word. Used to end the turn early instead of waiting out the full silence timeout."""
    import re

    t = text.strip().rstrip("\"')")
    if not t or t.endswith(("...", "…")) or t[-1] not in ".?!":
        return False
    words = re.findall(r"[a-z']+", t.lower())
    return not (words and words[-1] in _DANGLING)
