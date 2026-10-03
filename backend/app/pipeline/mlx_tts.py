"""Client for the MLX Kokoro sidecar (mlx_tts_worker.py): same Kokoro-82M voice as the ONNX path, ~3-4x faster on Apple
Silicon (first audio of a short phrase ~0.13 s vs ~0.45-0.85 s on CPU/ONNX). Optional: if the sidecar venv is missing or
dies, `FallbackTTS` transparently uses the ONNX engine.

Setup:  ./backend/scripts_setup_tts_mlx.sh     (creates <repo>/.venv-tts with Python 3.11 + mlx-audio)
Env:    MIRAGE_TTS=auto|mlx|onnx (default auto), MIRAGE_TTS_PYTHON=<python with mlx-audio>,
        MIRAGE_TTS_WORKERS=<n sidecar processes, default 2>: one sidecar synthesises one request at a time, so concurrent
        conversations queue behind each other; a request goes to the least busy sidecar (capacity work, see
        docs/overnight/capacity.md for the measured effect and the memory cost of each extra process).
"""
import asyncio
import json
import os
import struct
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKER = Path(__file__).with_name("mlx_tts_worker.py")


def sidecar_python() -> str | None:
    p = os.environ.get("MIRAGE_TTS_PYTHON") or str(REPO_ROOT / ".venv-tts" / "bin" / "python")
    return p if Path(p).exists() else None


class _Sidecar:
    """One sidecar process (one request synthesised at a time)."""
    sample_rate = 24000

    def __init__(self, python: str | None = None):
        self.python = python or sidecar_python()
        if not self.python:
            raise RuntimeError("MLX TTS sidecar python not found (run backend/scripts_setup_tts_mlx.sh)")
        self.proc: asyncio.subprocess.Process | None = None
        self._queues: dict[int, asyncio.Queue] = {}
        self._next = 1
        self._reader: asyncio.Task | None = None
        self._start_lock: asyncio.Lock | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self.inflight = 0

    async def start(self, timeout: float = 90.0) -> None:
        loop = asyncio.get_running_loop()
        if self._loop is not loop or self._start_lock is None:
            self._start_lock = asyncio.Lock()
            if self.proc and self.proc.returncode is None:  # was started under another event loop: restart cleanly
                await self.close()
            self._loop = loop
        async with self._start_lock:
            if self.proc and self.proc.returncode is None:
                return
            self.proc = await asyncio.create_subprocess_exec(
                self.python, str(WORKER), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL, limit=2 ** 24)
            ready = loop.create_future()
            self._reader = asyncio.create_task(self._read_loop(ready, self.proc))
            try:
                await asyncio.wait_for(ready, timeout)
            except Exception:
                await self.close()
                raise RuntimeError("MLX TTS sidecar failed to start")

    async def _read_loop(self, ready: asyncio.Future, proc) -> None:
        out = proc.stdout
        try:
            while True:
                rid, kind, n = struct.unpack("<III", await out.readexactly(12))
                payload = await out.readexactly(n) if n else b""
                if kind == 3:
                    if not ready.done():
                        ready.set_result(True)
                    continue
                q = self._queues.get(rid)
                if q is not None:
                    q.put_nowait((kind, payload))
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:  # sidecar gone: fail everything in flight
            if not ready.done():
                ready.set_exception(RuntimeError("sidecar exited"))
            for q in self._queues.values():
                q.put_nowait((2, b"sidecar exited"))

    async def synthesize(self, text: str, voice: str = "af_heart"):
        await self.start()
        rid = self._next
        self._next += 1
        q: asyncio.Queue = asyncio.Queue()
        self._queues[rid] = q
        finished = False
        self.inflight += 1
        try:
            self.proc.stdin.write((json.dumps({"id": rid, "text": text, "voice": voice}) + "\n").encode())
            await self.proc.stdin.drain()
            while True:
                kind, payload = await q.get()
                if kind == 0:
                    yield payload
                elif kind == 1:
                    finished = True
                    return
                else:
                    finished = True
                    raise RuntimeError(payload.decode(errors="replace"))
        finally:
            self.inflight -= 1
            self._queues.pop(rid, None)
            if not finished:  # barge-in / cancelled consumer: tell the sidecar to stop working on it
                try:
                    if self.proc and self.proc.returncode is None:
                        self.proc.stdin.write((json.dumps({"cancel": rid}) + "\n").encode())
                except Exception:  # noqa: BLE001
                    pass

    def warmup(self) -> None:  # the sidecar warms itself up before it reports ready
        pass

    async def close(self) -> None:
        p, self.proc = self.proc, None
        if p and p.returncode is None:
            try:
                p.stdin.close()
                await asyncio.wait_for(p.wait(), 3)
            except Exception:
                p.kill()
        if self._reader:
            self._reader.cancel()


class MlxKokoroTTS:
    """Pool of sidecars with the old single-sidecar API (`start`, `synthesize`, `close`)."""
    sample_rate = 24000

    def __init__(self, python: str | None = None, workers: int | None = None):
        n = workers if workers is not None else int(os.environ.get("MIRAGE_TTS_WORKERS", "2"))
        self.workers = [_Sidecar(python) for _ in range(max(1, n))]
        self._rr = 0

    @property
    def proc(self):  # back-compat: the first sidecar's process
        return self.workers[0].proc

    async def start(self, timeout: float = 90.0) -> None:
        await asyncio.gather(*[w.start(timeout) for w in self.workers])

    def _pick(self) -> _Sidecar:
        """Least busy sidecar that is alive (round-robin among equals)."""
        live = [w for w in self.workers if w.proc is None or w.proc.returncode is None] or self.workers
        low = min(w.inflight for w in live)
        cands = [w for w in live if w.inflight == low]
        self._rr += 1
        return cands[self._rr % len(cands)]

    async def synthesize(self, text: str, voice: str = "af_heart"):
        w = self._pick()
        async for c in w.synthesize(text, voice):
            yield c

    def warmup(self) -> None:
        pass

    async def close(self) -> None:
        await asyncio.gather(*[w.close() for w in self.workers], return_exceptions=True)


class FallbackTTS:
    """Use `primary`; if it fails before producing any audio, serve the request (and later ones) from `secondary_factory()`."""

    def __init__(self, primary, secondary_factory):
        self.primary, self._mk, self._secondary = primary, secondary_factory, None
        self.sample_rate = 24000
        self.broken = False

    async def _second(self):
        if self._secondary is None:
            self._secondary = await asyncio.to_thread(self._mk)
        return self._secondary

    async def synthesize(self, text: str, voice: str = "af_heart"):
        if not self.broken:
            got = False
            try:
                async for c in self.primary.synthesize(text, voice):
                    got = True
                    yield c
                return
            except asyncio.CancelledError:
                raise
            except Exception:
                if got:
                    return  # partial audio already sent: do not double-speak
                self.broken = True
        async for c in (await self._second()).synthesize(text, voice):
            yield c

    def warmup(self) -> None:
        pass
