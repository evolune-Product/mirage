"""Echo simulation: the agent's TTS audio is mixed back into the 'microphone' at -10/-18/-26 dB with delay, reverb and a
touch of nonlinearity, and fed through the real Session (Silero VAD) while the agent is 'speaking'. Counts false
barge-ins / false user turns, and checks that a real interruption on top of the echo is still detected.

  cd backend && MIRAGE_VAD=silero .venv/bin/python scripts_echo_sim.py [--app-root <dir with app/>] [--no-ref]

--app-root runs the same simulation against another checkout (A/B against the pre-change code: `git archive HEAD`).
Needs /tmp/mirage_bench_cache/q*.pcm (written by scripts_latency.py): 12 Kokoro-synthesised 16 kHz utterances."""
import argparse
import asyncio
import os
import statistics
import sys
from pathlib import Path

import numpy as np

ap = argparse.ArgumentParser()
ap.add_argument("--app-root", default=str(Path(__file__).resolve().parent))
ap.add_argument("--no-ref", action="store_true", help="disable the reference-based echo test (keep the rest)")
ap.add_argument("--quick", action="store_true")
args = ap.parse_args()
os.environ.setdefault("MIRAGE_VAD", "silero")
sys.path.insert(0, args.app_root)
from app.pipeline import session as S  # noqa: E402

if args.no_ref:
    from app.pipeline import echo as E

    E.ENABLED = False
SR, FR = 16000, 320
rng = np.random.default_rng(11)
CLIPS = [np.frombuffer(p.read_bytes(), np.int16).astype(np.float32) for p in sorted(Path("/tmp/mirage_bench_cache").glob("q*.pcm"))]


def up24(x):
    return np.interp(np.linspace(0, len(x), int(len(x) * 1.5), endpoint=False), np.arange(len(x)), x)


def room(x, delay_ms, db):
    """Speaker->mic path: delay, short reverb tail, mild clipping (speaker/mic nonlinearity), gain in dB."""
    d = int(SR * delay_ms / 1000)
    ir = np.zeros(int(SR * 0.08))
    ir[0] = 1.0
    ir[1:] = rng.normal(0, 0.25, len(ir) - 1) * np.exp(-np.arange(1, len(ir)) / (SR * 0.02))
    y = np.convolve(x, ir)[: len(x)]
    y = np.tanh(y / 12000.0) * 12000.0
    return np.concatenate([np.zeros(d), y * 10 ** (db / 20)])[: len(x)]


class NoSTT:
    async def transcribe(self, pcm, sr=16000):
        return ""


class Cap:
    def __init__(self):
        self.ev = []

    async def j(self, m):
        self.ev.append((self.frame, m["type"]))

    async def b(self, b):
        pass


async def trial(agent16, db, delay, user16=None, user_at=1.6):
    cap = Cap()
    cap.frame = 0
    sess = S.Session(S.Providers(NoSTT(), None, None), "", "v", cap.j, cap.b)
    n = len(agent16) // FR
    mic = room(agent16, delay, db)
    total = n + 60
    mic = np.concatenate([mic, np.zeros(total * FR - len(mic))]) + rng.normal(0, 110, total * FR)
    onset = None
    if user16 is not None:
        o = int(user_at * SR)
        mic[o:o + len(user16)] += user16[: len(mic) - o]
        onset = o + int(np.flatnonzero(np.abs(user16) > 1200)[0])
    # agent starts talking at frame 0: server sends all audio at once, the reply task stays 'running' for n frames
    done = asyncio.Event()
    sess.reply_task = asyncio.create_task(done.wait())
    a24 = up24(agent16).astype(np.int16).tobytes()
    for i in range(0, len(a24), 24000 * 2):  # 1 s chunks, as the TTS stage would produce them
        await sess.send_bytes(a24[i:i + 24000 * 2])
    mic16 = np.clip(mic, -32768, 32767).astype(np.int16).tobytes()
    for k in range(total):
        cap.frame = k
        if k == n:
            done.set()
            await asyncio.sleep(0)
        await sess._frame(mic16[k * FR * 2:(k + 1) * FR * 2])
    sess.reply_task.cancel()
    barge = [f for f, t in cap.ev if t == "interrupted"]
    starts = [f for f, t in cap.ev if t == "speech_start"]
    echo_flag = any(t == "echo" for _, t in cap.ev)
    lat = None
    if onset is not None:
        after = [f for f in barge if f * FR >= onset - FR * 2]
        lat = (after[0] * FR - onset) / SR * 1000 if after else None
    return {"false_barge": len(barge) > 0 and user16 is None, "false_turn": len(starts) > 0 and user16 is None,
            "barge": barge, "lat": lat, "echo_flag": echo_flag, "starts": starts}


async def main():
    levels = [-10, -18, -26]
    delays = [100, 250, 450]
    nclip = 4 if args.quick else 12
    print(f"app: {args.app_root}  ref={'off' if args.no_ref else 'on'}  vad=silero  clips={nclip}")
    print("== echo only (no user speaking): false barge-ins / false user turns / 'use headphones' flag raised ==")
    print(f"{'echo dB':>8} {'delay ms':>9} {'runs':>5} {'false barge':>12} {'false turn':>11} {'hint flag':>10}")
    tot = {"fb": 0, "ft": 0, "n": 0}
    for db in levels:
        for d in delays:
            r = []
            for i in range(nclip):
                a = np.concatenate([CLIPS[i], np.zeros(8000), CLIPS[(i + 5) % len(CLIPS)]])
                r.append(await trial(a, db, d))
            fb, ft, fl = sum(x["false_barge"] for x in r), sum(x["false_turn"] for x in r), sum(x["echo_flag"] for x in r)
            if os.environ.get("SIM_V"):
                print("   barge frames:", [x["barge"] for x in r if x["false_barge"]])
            tot["fb"] += fb; tot["ft"] += ft; tot["n"] += len(r)
            print(f"{db:8d} {d:9d} {len(r):5d} {fb:9d}/{len(r)} {ft:8d}/{len(r)} {fl:7d}/{len(r)}")
    print(f"TOTAL false barge-ins {tot['fb']}/{tot['n']} ({100 * tot['fb'] / tot['n']:.0f}%), false user turns {tot['ft']}/{tot['n']}")
    print("\n== real user interrupts at 1.6 s while the echo is present (should be detected; latency from first audible user sample) ==")
    for db in levels:
        hit, lats = 0, []
        for i in range(nclip):
            a = np.concatenate([CLIPS[i], np.zeros(8000), CLIPS[(i + 5) % len(CLIPS)]])
            u = CLIPS[(i + 3) % len(CLIPS)]
            u = u * (2400 / max(np.sqrt((u[np.abs(u) > 800] ** 2).mean()), 1))  # a normally loud user
            r = await trial(a, db, 250, u)
            if r["lat"] is not None:
                hit += 1; lats.append(r["lat"])
        print(f"echo {db:4d} dB: detected {hit}/{nclip}, median latency {statistics.median(lats) if lats else float('nan'):.0f} ms")


asyncio.run(main())
