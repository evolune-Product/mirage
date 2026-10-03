"""Client for the MLX Kokoro sidecar (mlx_tts_worker.py): same Kokoro-82M voice as the ONNX path, ~3-4x faster on Apple
Silicon (first audio of a short phrase ~0.13 s vs ~0.45-0.85 s on CPU/ONNX). Optional: if the sidecar venv is missing or
dies, `FallbackTTS` transparently uses the ONNX engine.

Setup:  ./backend/scripts_setup_tts_mlx.sh     (creates <repo>/.venv-tts with Python 3.11 + mlx-audio)
Env:    MIRAGE_TTS=auto|mlx|onnx (default auto), MIRAGE_TTS_PYTHON=<python with mlx-audio>
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


class MlxKokoroTTS:
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
        try:
            self.proc.stdin.write((json.dumps({"id": rid, "text": text, "voice": voice}) + "\n").encode())
            await self.proc.stdin.drain()
            while True:
                kind, payload = await q.get()
                if kind == 0:
                    yield payload
                elif kind == 1:
                    return
                else:
                    raise RuntimeError(payload.decode(errors="replace"))
        finally:
            self._queues.pop(rid, None)

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
