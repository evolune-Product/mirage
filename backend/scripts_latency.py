"""Repeatable end-to-end voice latency benchmark (drives the REAL Session with real STT/LLM/TTS).

  cd backend && .venv/bin/python scripts_latency.py --n 10 [--lipsync r_xxx] [--json out.json]

A synthetic user (Kokoro-voiced questions, fed as 20 ms frames at real-time pace) speaks, then goes silent.
All times are measured from the END of the user's last speech sample, so they are what a person feels:

  endpoint  last speech sample -> turn-taker declares end of turn (VAD hangover)
  stt       end of turn -> transcript (includes queueing before STT starts)
  llm_ft    transcript -> first LLM token
  sent      first LLM token -> first speakable chunk handed to TTS
  tts       chunk handed to TTS -> first audio produced
  lip       first audio produced -> first video_segment ready (only with --lipsync)
  first_audio  last speech sample -> first agent audio byte on the wire   <- headline (a)
  first_video  last speech sample -> first lip-synced frame on the wire   <- headline (b)

Env knobs (so before/after runs are one command): MIRAGE_APP_ROOT (alternate code tree, e.g. a baseline copy),
MIRAGE_LLM, MIRAGE_STT_MODEL, MIRAGE_END_OF_TURN_MS.
"""
import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
root = os.environ.get("MIRAGE_APP_ROOT")
if root:  # alternate code tree (e.g. a pre-optimisation snapshot) wins over backend/app
    sys.path.insert(0, root)

import numpy as np  # noqa: E402

import app.pipeline.session as S  # noqa: E402
from app.pipeline.session import Providers, Session, get_providers  # noqa: E402

QUESTIONS = [
    "Hi, can you tell me what a digital twin is?",
    "How much does the starter plan cost per month?",
    "What makes your video agents feel more natural than a chatbot?",
    "Can I use my own face and voice for the agent?",
    "How long does it take to set everything up?",
    "Does it work on mobile phones as well?",
    "Tell me a little about what your company does.",
    "What happens when I interrupt the agent while it is speaking?",
    "Is my data private and who can see my conversations?",
    "Could you explain the difference between the pro plan and the starter plan?",
    "Which languages do you support right now?",
    "Why should I choose this over a regular video call?",
]
SYSTEM = ("You are a friendly sales rep for Mirage. Mirage Starter costs 19 dollars per month with 120 minutes; "
          "Pro costs 79 dollars per month. Answer in one or two short sentences.\n\n"
          "You are speaking aloud in a live voice conversation. Reply in at most two short spoken sentences "
          "(under 35 words total). No markdown, lists or emojis.")
CACHE = Path(os.environ.get("MIRAGE_BENCH_CACHE", "/tmp/mirage_bench_cache"))


def to16k(pcm24: bytes) -> bytes:
    a = np.frombuffer(pcm24, np.int16).astype(np.float32)
    n = int(len(a) * 16000 / 24000)
    return np.interp(np.linspace(0, len(a), n, endpoint=False), np.arange(len(a)), a).astype(np.int16).tobytes()


async def make_utterances(tts, n: int) -> list[bytes]:
    CACHE.mkdir(parents=True, exist_ok=True)
    out = []
    for i in range(n):
        q = QUESTIONS[i % len(QUESTIONS)]
        f = CACHE / f"q{i % len(QUESTIONS)}.pcm"
        if not f.exists():
            raw = b"".join([c async for c in tts.synthesize(q, "af_heart")])
            f.write_bytes(to16k(raw))
        out.append(f.read_bytes())
    return out


class Probe:
    """Wraps providers to timestamp stage boundaries without changing behaviour."""

    def __init__(self, base: Providers):
        self.t: dict[str, float] = {}
        probe = self

        class STT:
            async def transcribe(s, pcm, sr=16000):
                probe.t.setdefault("stt_start", time.monotonic())
                r = await base.stt.transcribe(pcm, sr)
                probe.t.setdefault("stt_end", time.monotonic())
                return r

            def __getattr__(s, k):
                return getattr(base.stt, k)

        class LLM:
            async def stream(s, system, history, user):
                probe.t.setdefault("llm_start", time.monotonic())
                async for tok in base.llm.stream(system, history, user):
                    probe.t.setdefault("llm_first_tok", time.monotonic())
                    yield tok

            def __getattr__(s, k):
                return getattr(base.llm, k)

        class TTS:
            async def synthesize(s, text, voice="default"):
                probe.t.setdefault("tts_start", time.monotonic())
                async for c in base.tts.synthesize(text, voice):
                    probe.t.setdefault("tts_first", time.monotonic())
                    yield c

            def __getattr__(s, k):
                return getattr(base.tts, k)

        self.providers = Providers(STT(), LLM(), TTS())


async def one_run(pcm16: bytes, base: Providers, lip, tail_s: float = 12.0) -> dict:
    probe = Probe(base)
    ev: dict[str, float] = {}
    done = asyncio.Event()

    async def sj(m):
        now = time.monotonic()
        t = m["type"]
        if t == "transcript" and m["role"] == "user":
            ev.setdefault("transcript", now)
        elif t == "video_segment":
            ev.setdefault("first_video", now)
        elif t == "agent_done" or t == "error":
            ev.setdefault("done", now)
            if t == "error":
                ev["error"] = m["message"]
            done.set()
        elif t in ("speech_start", "interrupted"):
            ev.setdefault(t, now)

    async def sb(b):
        ev.setdefault("first_audio", time.monotonic())

    kw = {"end_of_turn_ms": int(os.environ["MIRAGE_END_OF_TURN_MS"])} if os.environ.get("MIRAGE_END_OF_TURN_MS") else {}
    sess = Session(probe.providers, SYSTEM, "af_heart", sj, sb, lipsync=lip, **kw)
    if hasattr(sess, "warmup") and not os.environ.get("MIRAGE_BENCH_NO_WARMUP"):
        await sess.warmup()
    orig = sess._start_turn

    async def spy(*a, **k):
        ev.setdefault("eot", time.monotonic())
        await orig(*a, **k)

    sess._start_turn = spy
    FR = S.FRAME_BYTES
    frames = [pcm16[i:i + FR] for i in range(0, len(pcm16) // FR * FR, FR)]
    sil = bytes(FR)
    start = time.monotonic()
    k = 0
    for f in frames:  # real-time pacing
        await sess.feed(f)
        k += 1
        await asyncio.sleep(max(0.0, start + k * 0.02 - time.monotonic()))
    t_speech_end = time.monotonic()
    # trim trailing near-silence of the synthetic voice so "end of speech" is where the energy actually stops
    for f in reversed(frames):
        if np.abs(np.frombuffer(f, np.int16)).max() > 700:
            break
        t_speech_end -= 0.02
    deadline = t_speech_end + tail_s
    while not done.is_set() and time.monotonic() < deadline:
        k += 1
        await sess.feed(sil)
        await asyncio.sleep(max(0.0, start + k * 0.02 - time.monotonic()))
    await sess.close()
    T, E = probe.t, ev
    r = {"error": E.get("error"), "n_audio_first": "first_audio" in E}
    def d(a, b):
        return (b - a) * 1000 if a is not None and b is not None else None
    r["endpoint"] = d(t_speech_end, E.get("eot"))
    r["stt"] = d(E.get("eot"), E.get("transcript"))
    r["llm_ft"] = d(T.get("llm_start"), T.get("llm_first_tok"))
    r["sent"] = d(T.get("llm_first_tok"), T.get("tts_start"))
    r["tts"] = d(T.get("tts_start"), T.get("tts_first"))
    if lip:
        r["lip"] = d(T.get("tts_first"), E.get("first_video"))
    r["first_audio"] = d(t_speech_end, E.get("first_audio"))
    if lip:
        r["first_video"] = d(t_speech_end, E.get("first_video"))
    r["total"] = d(t_speech_end, E.get("done"))
    return r


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))]


def summarize(rows: list[dict]) -> dict:
    out = {}
    for k in ["endpoint", "stt", "llm_ft", "sent", "tts", "lip", "first_audio", "first_video", "total"]:
        xs = [r[k] for r in rows if r.get(k) is not None]
        if xs:
            out[k] = {"median": round(statistics.median(xs)), "p90": round(pct(xs, 90)), "min": round(min(xs)), "n": len(xs)}
    return out


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--lipsync", default="", help="replica id to render lips for (needs lipsync server)")
    ap.add_argument("--json", default="")
    ap.add_argument("--warm", type=int, default=1)
    a = ap.parse_args()
    t0 = time.time()
    base = get_providers(os.environ.get("MIRAGE_LLM_SPEC", ""))
    print(f"providers loaded {time.time() - t0:.1f}s  code={S.__file__}")
    lip = None
    if a.lipsync:
        from app.pipeline.lipsync import LipsyncClient
        lip = LipsyncClient(a.lipsync)
        if not await LipsyncClient.available():
            sys.exit("lipsync server not reachable")
        await lip.idle()
    utts = await make_utterances(base.tts, a.n + a.warm)
    rows = []
    for i, u in enumerate(utts):
        r = await one_run(u, base, lip)
        tag = "warm" if i < a.warm else f"run{i - a.warm + 1:02d}"
        print(tag, {k: (round(v) if isinstance(v, float) else v) for k, v in r.items() if v is not None})
        if i >= a.warm:
            rows.append(r)
        if lip and r.get("first_video") is None:
            lip = None if r.get("error") else lip
    summ = summarize(rows)
    print(f"\n{'stage':12} {'median':>8} {'p90':>8} {'min':>8}  (ms, n={len(rows)})")
    for k, v in summ.items():
        print(f"{k:12} {v['median']:8d} {v['p90']:8d} {v['min']:8d}")
    if a.json:
        Path(a.json).write_text(json.dumps({"summary": summ, "rows": rows}, indent=1))


asyncio.run(main())
