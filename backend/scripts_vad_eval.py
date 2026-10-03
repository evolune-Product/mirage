"""Compare the energy gate with Silero VAD on noise rejection and speech-onset / barge-in latency.

  cd backend && .venv/bin/python scripts_vad_eval.py          (needs /tmp/mirage_bench_cache/q*.pcm from scripts_latency.py)

Scenarios (all 16 kHz int16, fed as 20 ms frames exactly like Session does):
  noise    60 s of keyboard clicks / fan hiss / hum / loud hiss / music-like chord: false 'speech' frames and false
           barge-ins (speech sustained for the barge-in window while the agent is talking)
  speech   12 real(istic) Kokoro utterances after 1 s of room noise: delay from true speech onset to the first 'speech'
           frame, and to a barge-in
  echo     the same utterances played back 18 dB down (what leaks through a poor echo canceller) -- both detectors fire,
           this is the known limit of any single-channel VAD (reported, not hidden)
"""
import statistics
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from app.pipeline.turn_taking import EnergyVAD, SileroVAD, TurnTaker  # noqa: E402

CACHE = Path("/tmp/mirage_bench_cache")
FR = 640
rng = np.random.default_rng(7)
SR = 16000


def pcm(x):
    return np.clip(x, -32768, 32767).astype(np.int16).tobytes()


def frames(b):
    return [b[i:i + FR] for i in range(0, len(b) // FR * FR, FR)]


def noises(sec=12):
    n = SR * sec
    t = np.arange(n) / SR
    clicks = np.zeros(n)
    for k in range(0, n - 200, int(SR * 0.23)):  # typing: ~4 keystrokes/s
        clicks[k:k + 160] = rng.normal(0, 9000, 160) * np.exp(-np.arange(160) / 30)
    return {
        "keyboard clicks": clicks + rng.normal(0, 60, n),
        "fan hiss (rms 350)": rng.normal(0, 350, n),
        "loud hiss (rms 1500)": rng.normal(0, 1500, n),
        "50 Hz hum + hiss": 1200 * np.sin(2 * np.pi * 50 * t) + rng.normal(0, 200, n),
        "music chord": sum(2500 * np.sin(2 * np.pi * f * t) for f in (261.6, 329.6, 392.0)) + rng.normal(0, 100, n),
    }


def run(vad, sig_pcm, agent_talking=False, barge_frames=None):
    tk = TurnTaker(end_of_turn_ms=700, vad=vad, pause_ms=150)
    on = 0.7 if agent_talking else 0.5
    tk.on = on
    tk.min_rms = 600.0 if (agent_talking and isinstance(vad, SileroVAD)) else 0.0
    sp = 0
    first = None
    barge = None
    n_speech = 0
    for i, f in enumerate(frames(sig_pcm)):
        ev = tk.push(f, 20)
        if ev == "speech":
            n_speech += 1
            sp += 1
            first = i if first is None else first
            if barge is None and barge_frames and sp >= barge_frames:
                barge = i
        else:
            sp = 0
    return n_speech, first, barge


def main():
    mk = {"energy": lambda: EnergyVAD(500.0), "silero": SileroVAD}
    bf = {"energy": 5, "silero": 5}
    print("== noise (12 s each, agent talking => barge-in counted) ==")
    print(f"{'noise':24} {'energy: speech frames / false barge-ins':>42} {'silero':>26}")
    for name, sig in noises().items():
        row = []
        for k in ("energy", "silero"):
            ns, _, barge = run(mk[k](), pcm(sig), True, bf[k])
            row.append(f"{ns:4d} / {'YES' if barge is not None else 'no ':3}")
        print(f"{name:24} {row[0]:>42} {row[1]:>26}")
    qs = sorted(CACHE.glob("q*.pcm"))
    if not qs:
        print("no speech cache; run scripts_latency.py once")
        return
    print("\n== speech onset after 1 s room noise (ms) ==")
    res = {k: {"onset": [], "barge": []} for k in mk}
    echo = {k: 0 for k in mk}
    for q in qs:
        sp = np.frombuffer(q.read_bytes(), np.int16).astype(np.float32)
        truth = int(np.flatnonzero(np.abs(sp) > 1200)[0]) / SR * 1000  # first clearly audible sample
        sig = np.concatenate([rng.normal(0, 150, SR), sp + rng.normal(0, 150, len(sp)), rng.normal(0, 150, SR)])
        for k in mk:
            _, first, barge = run(mk[k](), pcm(sig), True, bf[k])
            if first is not None:
                res[k]["onset"].append(first * 20 - 1000 - truth)
            if barge is not None:
                res[k]["barge"].append(barge * 20 + 20 - 1000 - truth)
            ns, _, bg = run(mk[k](), pcm(np.concatenate([rng.normal(0, 150, SR), sp * 0.126 + rng.normal(0, 150, len(sp))])), True, bf[k])
            echo[k] += bg is not None
    for k in mk:
        o, b = res[k]["onset"], res[k]["barge"]
        print(f"{k:7} detected {len(o)}/{len(qs)}  first 'speech' frame vs onset: median {statistics.median(o):.0f} ms  |  "
              f"barge-in fires: median {statistics.median(b):.0f} ms after onset ({len(b)}/{len(qs)})")
    print("\n== echo of agent speech 18 dB down (leaks past a bad AEC) ==")
    for k in mk:
        print(f"{k:7} false barge-ins: {echo[k]}/{len(qs)}")


main()
