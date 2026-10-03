"""Mirage load generator: N simultaneous real WebSocket conversations replaying recorded 16 kHz question audio.

    cd backend && .venv/bin/python -m loadtest.loadgen --base http://localhost:8440 --db /tmp/cap.db \
        --replica r_035d420020e3 --levels 1,2,3,5,8 --mode both --turns 3 --api-log /tmp/cap_api.log

Each virtual user: signs up (own account, so credits/rate limits do not couple users), opens a conversation, connects to
/v1/conversations/{cid}/stream with the same `hello` the browser sends (tagged binary video), then for each turn streams one
question at real-time pace (20 ms frames), keeps the mic open with silence, waits for `agent_done`, "listens" for a few
seconds and asks the next one. Measured per turn: end of user speech -> first agent audio byte, -> first video frame, total
turn time, dropped turns (no agent_done within the timeout), error events, close codes. A sampler records CPU / RSS of the
API, lipsync and Ollama processes and the Apple GPU utilisation (ioreg) once a second.
Question audio is synthesised once with Kokoro and cached in loadtest/audio_cache/ (not committed).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import statistics
import struct
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import numpy as np

HERE = Path(__file__).resolve().parent
CACHE = HERE / "audio_cache"
QUESTIONS = [
    "Hi there, can you tell me what a digital twin is?",
    "What would you say is the biggest advantage of talking to you over reading a manual?",
    "How much does the starter plan cost per month?",
    "Can you explain in simple words how you understand what I am saying?",
    "Tell me something interesting about your day.",
    "Why should a small business care about video agents?",
]
FRAME = 640  # 20 ms of 16 kHz int16
SILENCE = bytes(FRAME)


def question_audio(i: int) -> bytes:
    """16 kHz int16 PCM of QUESTIONS[i % n] (synthesised once with Kokoro, then cached on disk)."""
    CACHE.mkdir(exist_ok=True)
    p = CACHE / f"q{i % len(QUESTIONS)}.raw"
    if p.exists():
        return p.read_bytes()
    from app.pipeline.local import KokoroTTS

    async def go():
        tts = KokoroTTS()
        return b"".join([c async for c in tts.synthesize(QUESTIONS[i % len(QUESTIONS)])])

    a = np.frombuffer(asyncio.run(go()), np.int16).astype(np.float32)
    pcm16 = np.interp(np.linspace(0, len(a), int(len(a) * 16 / 24), endpoint=False), np.arange(len(a)), a).astype(np.int16).tobytes()
    p.write_bytes(pcm16)
    return pcm16


def _voiced_end(pcm: bytes) -> int:
    a = np.frombuffer(pcm, np.int16)
    idx = np.flatnonzero(np.abs(a) > 800)
    return int(idx[-1]) * 2 if len(idx) else len(pcm)


@dataclass
class Turn:
    ttfa: float | None = None
    ttff: float | None = None  # first video frame (video_segment)
    total: float | None = None
    ok: bool = False
    errors: list = field(default_factory=list)


@dataclass
class UserResult:
    uid: int
    turns: list = field(default_factory=list)
    connect_s: float | None = None
    ready_s: float | None = None
    close_code: int | None = None
    fatal: str | None = None
    busy: bool = False
    video_bytes: int = 0
    audio_bytes: int = 0
    live_face: bool = False


async def run_user(uid: int, base: str, replica_db: dict | None, face: bool, turns: int, think_s: float, turn_timeout: float,
                   stagger: float, stop_at: float | None = None, slow_client_s: float = 0.0, key_hdr: dict | None = None) -> UserResult:
    import websockets

    r = UserResult(uid)
    await asyncio.sleep(stagger)
    try:
        async with httpx.AsyncClient(base_url=base + "/v1", timeout=30) as h:
            sj = (await h.post("/signup", json={"email": f"load{uid}_{int(time.time()*1000)}@x.io"})).json()
            key = sj["api_key"]
            H = {"x-api-key": key}
            rid = None
            if face and replica_db:
                rid = replica_db["register"](sj.get("id") or sj.get("account_id"), key)
            body = {"name": f"L{uid}", "system_prompt": "You are a friendly, concise assistant for a software company.", "llm": "ollama/llama3.2:1b"}
            if rid:
                body["replica_id"] = rid
            p = (await h.post("/personas", json=body, headers=H)).json()
            cid = (await h.post("/conversations", json={"persona_id": p["id"]}, headers=H)).json()["id"]
    except Exception as e:  # noqa: BLE001
        r.fatal = f"setup: {type(e).__name__}: {e}"
        return r
    url = base.replace("http", "ws", 1) + f"/v1/conversations/{cid}/stream?api_key={key}"
    t_conn = time.monotonic()
    try:
        async with websockets.connect(url, max_size=None, ping_interval=None, open_timeout=30) as ws:
            r.connect_s = time.monotonic() - t_conn
            await ws.send(json.dumps({"type": "hello", "framing": "tagged", "audio": {"resampled": True}, "tier": 1}))
            state = {"turn": None, "t_eot": None, "ready": asyncio.Event(), "done": asyncio.Event()}

            async def recv():
                try:
                    async for m in ws:
                        if slow_client_s:
                            await asyncio.sleep(slow_client_s)
                        tn: Turn | None = state["turn"]
                        now = time.monotonic()
                        if isinstance(m, bytes):
                            tag = m[0] if m else 0
                            if tag == 2:
                                r.video_bytes += len(m)
                                (n,) = struct.unpack(">I", m[1:5])
                                kind = json.loads(m[5:5 + n]).get("t")
                                if kind == "video_segment" and tn and tn.ttff is None and state["t_eot"]:
                                    tn.ttff = now - state["t_eot"]
                            else:
                                r.audio_bytes += len(m)
                                if tn and tn.ttfa is None and state["t_eot"]:
                                    tn.ttfa = now - state["t_eot"]
                            continue
                        d = json.loads(m)
                        t = d.get("type")
                        if t == "ready":
                            r.ready_s = now - t_conn
                            r.live_face = bool(d.get("live_face"))
                            state["ready"].set()
                        elif t == "busy":
                            r.busy = True
                        elif t == "error" and tn:
                            tn.errors.append(d.get("message", "")[:120])
                        elif t == "agent_done" and tn:
                            tn.total = now - (state["t_eot"] or now)
                            tn.ok = tn.ttfa is not None
                            state["done"].set()
                except Exception as e:  # noqa: BLE001
                    r.fatal = r.fatal or f"recv: {type(e).__name__}"
                r.close_code = getattr(ws, "close_code", None)
                state["ready"].set(); state["done"].set()

            rt = asyncio.create_task(recv())
            src = {"buf": b""}

            async def mic():
                nxt = time.monotonic()
                try:
                    while True:
                        if src["buf"]:
                            f, src["buf"] = src["buf"][:FRAME], src["buf"][FRAME:]
                            f = f.ljust(FRAME, b"\0")
                        else:
                            f = SILENCE
                        await ws.send(f)
                        nxt += 0.02
                        await asyncio.sleep(max(0, nxt - time.monotonic()))
                except Exception:  # noqa: BLE001
                    return

            mt = asyncio.create_task(mic())
            try:
                await asyncio.wait_for(state["ready"].wait(), 90)
            except asyncio.TimeoutError:
                r.fatal = "no ready within 90 s"
            if r.ready_s is not None and not r.fatal:
                await asyncio.sleep(1.0)
                for i in range(turns):
                    if stop_at and time.monotonic() > stop_at:
                        break
                    q = question_audio(uid + i)
                    tn = Turn()
                    state["turn"], state["t_eot"] = tn, None
                    state["done"].clear()
                    src["buf"] = q
                    # end of speech = when the last voiced frame leaves the mic
                    state["t_eot"] = time.monotonic() + _voiced_end(q) / 32000
                    try:
                        await asyncio.wait_for(state["done"].wait(), turn_timeout + len(q) / 32000)
                    except asyncio.TimeoutError:
                        tn.errors.append("timeout")
                    if rt.done():
                        r.turns.append(tn)
                        break
                    r.turns.append(tn)
                    await asyncio.sleep(think_s)
            try:
                await ws.send(json.dumps({"type": "end"}))
            except Exception:  # noqa: BLE001
                pass
            mt.cancel()
            await asyncio.gather(mt, return_exceptions=True)
            try:
                await ws.close()
            except Exception:  # noqa: BLE001
                pass
            rt.cancel()
            await asyncio.gather(rt, return_exceptions=True)
            r.close_code = r.close_code or getattr(ws, "close_code", None)
    except websockets.exceptions.InvalidStatus as e:
        r.fatal = f"http {e.response.status_code}"
    except websockets.exceptions.ConnectionClosed as e:
        r.close_code = e.rcvd.code if e.rcvd else None
        r.busy = r.close_code == 1013
        r.fatal = f"closed {r.close_code}"
    except Exception as e:  # noqa: BLE001
        r.fatal = f"{type(e).__name__}: {e}"[:150]
    if r.close_code == 1013:
        r.busy = True
    return r


# ----------------------------------------------------------------------------------------------- resource sampler
_ENV = {k: v for k, v in os.environ.items() if not k.startswith("Malloc")}  # silences "MallocStackLogging" chatter from ps/ioreg


def _ps_rows(pat: str) -> list[tuple[float, float]]:
    out = subprocess.run(["ps", "-axo", "pid=,pcpu=,rss=,command="], capture_output=True, text=True, env=_ENV).stdout
    rows = []
    for ln in out.splitlines():
        if re.search(pat, ln) and "grep" not in ln:
            p = ln.split(None, 3)
            try:
                rows.append((float(p[1]), float(p[2]) / 1024))
            except (ValueError, IndexError):
                pass
    return rows


def gpu_util() -> float | None:
    try:
        out = subprocess.run(["ioreg", "-r", "-d", "1", "-c", "IOAccelerator"], capture_output=True, text=True, timeout=3, env=_ENV).stdout
        m = re.search(r'"Device Utilization %"\s*=\s*(\d+)', out)
        return float(m.group(1)) if m else None
    except Exception:  # noqa: BLE001
        return None


class Sampler(threading.Thread):
    """1 Hz: CPU% (of one core, so 100 = one core) and RSS MB for api / lipsync / ollama, system load average, GPU %."""

    def __init__(self, api_pat: str, lip_pat: str):
        super().__init__(daemon=True)
        self.pats = {"api": api_pat, "lipsync": lip_pat, "ollama": r"ollama (runner|serve)"}
        self.samples: list[dict] = []
        self.stop_evt = threading.Event()

    def run(self):
        while not self.stop_evt.is_set():
            s = {"t": time.time(), "gpu": gpu_util(), "load1": os.getloadavg()[0]}
            for k, pat in self.pats.items():
                rows = _ps_rows(pat)
                s[k + "_cpu"] = sum(c for c, _ in rows)
                s[k + "_rss"] = sum(m for _, m in rows)
            self.samples.append(s)
            self.stop_evt.wait(1.0)

    def summary(self) -> dict:
        out = {}
        for k in ("api_cpu", "lipsync_cpu", "ollama_cpu", "gpu", "load1"):
            v = [s[k] for s in self.samples if s.get(k) is not None]
            out[k] = (round(statistics.mean(v), 1), round(max(v), 1)) if v else (None, None)
        for k in ("api_rss", "lipsync_rss", "ollama_rss"):
            v = [s[k] for s in self.samples if s.get(k) is not None]
            out[k] = round(max(v)) if v else None
        return out


def q(v: list[float], p: float) -> float | None:
    if not v:
        return None
    v = sorted(v)
    return v[min(len(v) - 1, int(round(p * (len(v) - 1))))]


def parse_stage_log(path: str | None, offset: int) -> dict:
    """Server stage timings (needs MIRAGE_LOG_VOICE=1 on the API): median of stt_s / llm_ft_s / chunk1_s / tts_ft_s per turn."""
    if not path or not os.path.exists(path):
        return {}
    with open(path, "rb") as f:
        f.seek(offset)
        txt = f.read().decode(errors="replace")
    vals: dict[str, list[float]] = {}
    for m in re.finditer(r"voice turn: (\{.*?\})", txt):
        try:
            d = eval(m.group(1), {"__builtins__": {}})  # noqa: S307 - our own log line (python dict repr)
        except Exception:  # noqa: BLE001
            continue
        for k, v in d.items():
            vals.setdefault(k, []).append(v)
    return {k: round(statistics.median(v), 2) for k, v in vals.items()}


def background_load(seconds: float = 3.0) -> dict:
    """What else is using this machine right now (other jobs, other agents): GPU % and load average before our load starts."""
    g = []
    t_end = time.time() + seconds
    while time.time() < t_end:
        v = gpu_util()
        if v is not None:
            g.append(v)
        time.sleep(0.5)
    top = sorted(((c, cmd) for c, cmd in _top_cpu()), reverse=True)[:3]
    return {"gpu": round(statistics.mean(g), 1) if g else None, "load1": round(os.getloadavg()[0], 1),
            "top": [f"{c:.0f}% {cmd[-60:]}" for c, cmd in top]}


def _top_cpu():
    out = subprocess.run(["ps", "-axo", "pcpu=,command="], capture_output=True, text=True, env=_ENV).stdout
    for ln in out.splitlines():
        p = ln.strip().split(None, 1)
        if len(p) == 2 and "loadgen" not in p[1] and not p[1].startswith("ps "):
            try:
                yield float(p[0]), p[1]
            except ValueError:
                pass


async def run_level(n: int, args, mk_replica, face: bool, sampler_pats) -> dict:
    bg_load = background_load()
    sampler = Sampler(*sampler_pats)
    log_off = os.path.getsize(args.api_log) if args.api_log and os.path.exists(args.api_log) else 0
    sampler.start()
    t0 = time.monotonic()
    rep = {"register": mk_replica} if (face and mk_replica) else None
    res = await asyncio.gather(*[run_user(i, args.base, rep, face, args.turns, args.think, args.turn_timeout, i * args.stagger)
                                 for i in range(n)])
    wall = time.monotonic() - t0
    sampler.stop_evt.set()
    sampler.join(timeout=3)
    turns = [t for r in res for t in r.turns]
    okt = [t for t in turns if t.ok and not t.errors]
    warm = [t for r in res for t in r.turns[1:] if t.ok and not t.errors]
    ttfa = [t.ttfa for t in okt]
    ttff = [t.ttff for t in okt if t.ttff is not None]
    wttfa = [t.ttfa for t in warm]
    wttff = [t.ttff for t in warm if t.ttff is not None]
    errs: dict[str, int] = {}
    for r in res:
        if r.fatal:
            errs[r.fatal] = errs.get(r.fatal, 0) + 1
        for t in r.turns:
            for e in t.errors:
                errs[e[:60]] = errs.get(e[:60], 0) + 1
    want = n * args.turns
    return {"n": n, "mode": "face" if face else "voice", "wall_s": round(wall, 1),
            "convos_ok": sum(1 for r in res if r.turns and len(r.turns) == args.turns and all(t.ok and not t.errors for t in r.turns)),
            "busy_rejected": sum(1 for r in res if r.busy), "live_face_users": sum(1 for r in res if r.live_face),
            "turns_ok": len(okt), "turns_want": want, "dropped": want - len(okt),
            "ttfa_med": q(ttfa, .5), "ttfa_p90": q(ttfa, .9), "ttff_med": q(ttff, .5), "ttff_p90": q(ttff, .9),
            "warm_ttfa_med": q(wttfa, .5), "warm_ttfa_p90": q(wttfa, .9), "warm_ttff_med": q(wttff, .5), "warm_ttff_p90": q(wttff, .9),
            "ready_med": q([r.ready_s for r in res if r.ready_s], .5),
            "video_MB": round(sum(r.video_bytes for r in res) / 1e6, 1),
            "errors": errs, "bg": bg_load, "res": sampler.summary(), "stages": parse_stage_log(args.api_log, log_off)}


def fmt(v, w=6):
    return f"{v:{w}.2f}" if isinstance(v, (int, float)) else f"{'-':>{w}}"


def print_table(rows: list[dict]) -> None:
    print("\nmode  N  convos_ok  turns_ok/want  busy | first-audio s med/p90 (warm med/p90) | first-frame s med/p90 (warm) | "
          "CPU% api/lip/ollama (mean) GPU% mean/max  RSS MB api/lip/ollama")
    for r in rows:
        s = r["res"]
        print(f"{r['mode']:5} {r['n']:2}  {r['convos_ok']:2}/{r['n']:<2}  {r['turns_ok']:3}/{r['turns_want']:<3}  {r['busy_rejected']:3} | "
              f"{fmt(r['ttfa_med'])}/{fmt(r['ttfa_p90'])} ({fmt(r['warm_ttfa_med'])}/{fmt(r['warm_ttfa_p90'])}) | "
              f"{fmt(r['ttff_med'])}/{fmt(r['ttff_p90'])} ({fmt(r['warm_ttff_med'])}) | "
              f"{s['api_cpu'][0]}/{s['lipsync_cpu'][0]}/{s['ollama_cpu'][0]} GPU {s['gpu'][0]}/{s['gpu'][1]}  "
              f"{s['api_rss']}/{s['lipsync_rss']}/{s['ollama_rss']}")
        if r["errors"]:
            print("      errors:", r["errors"])
        if r["stages"]:
            print("      server stages (median s):", r["stages"])
        if r.get("bg"):
            print(f"      background before run: GPU {r['bg']['gpu']}%  load1 {r['bg']['load1']}  top: {r['bg']['top']}")


def make_replica_registrar(db_path: str, replica_src: str, data_dir: str):
    """Own replica row per account (the persona must belong to the account); the files are shared through a symlink."""
    import sqlite3

    def register(acc_id: str, key: str) -> str:
        con = sqlite3.connect(db_path, timeout=30)
        if not acc_id:
            acc_id = con.execute("select id from account where api_key=?", (key,)).fetchone()[0]
        rid = "r_" + os.urandom(6).hex()
        con.execute("insert into replica (id, account_id, name, train_video_url, status, created_at) values (?,?,?,?,?,datetime('now'))",
                    (rid, acc_id, "load", "x", "ready"))
        con.commit(); con.close()
        link = Path(data_dir) / "replicas" / rid
        link.symlink_to(Path(data_dir) / "replicas" / replica_src)
        return rid

    return register


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="http://localhost:8440")
    ap.add_argument("--levels", default="1,2,3,5,8")
    ap.add_argument("--mode", default="both", choices=["voice", "face", "both"])
    ap.add_argument("--turns", type=int, default=3)
    ap.add_argument("--think", type=float, default=2.0, help="seconds the user listens/thinks between turns")
    ap.add_argument("--turn-timeout", type=float, default=45.0)
    ap.add_argument("--stagger", type=float, default=0.4, help="seconds between user starts")
    ap.add_argument("--db", default="/tmp/cap.db", help="sqlite file of the API under test (face mode registers replicas directly)")
    ap.add_argument("--data", default="/tmp/cap_data")
    ap.add_argument("--replica", default="r_035d420020e3", help="existing replica dir (with source.mp4) under --data/replicas")
    ap.add_argument("--api-log", default=None, help="API stderr log (run it with MIRAGE_LOG_VOICE=1) for per-stage timings")
    ap.add_argument("--api-pat", default=r"uvicorn app.main:app --port 8440")
    ap.add_argument("--lip-pat", default=r"lipsync_server:app --port 8441")
    ap.add_argument("--json", default=None, help="write raw results here")
    ap.add_argument("--repeat", type=int, default=1)
    a = ap.parse_args()
    for i in range(len(QUESTIONS)):
        question_audio(i)
    reg = make_replica_registrar(a.db, a.replica, a.data)
    rows = []
    for _ in range(a.repeat):
        for n in [int(x) for x in a.levels.split(",")]:
            for face in ([False, True] if a.mode == "both" else [a.mode == "face"]):
                print(f"... N={n} {'face' if face else 'voice'}", flush=True)
                rows.append(asyncio.run(run_level(n, a, reg, face, (a.api_pat, a.lip_pat))))
                print_table(rows[-1:])
                time.sleep(3)
    print("\n==== SUMMARY ====")
    print_table(rows)
    if a.json:
        Path(a.json).write_text(json.dumps(rows, indent=1, default=str))


if __name__ == "__main__":
    main()
