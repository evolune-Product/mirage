"""Client for the voice-cloning sidecar (clone_worker.py, run in the `.venv-clone` python).

One thread-based client serves both callers: the live conversation path (asyncio, streaming chunk by chunk, cancellable
on barge-in) and the offline worker / API threads (plain blocking calls). Frames: see clone_worker.py.

Setup:  ./backend/scripts_setup_clone.sh   (creates <repo>/.venv-clone with Python 3.11 + mlx-audio)
Env:    MIRAGE_CLONE_PYTHON=<python with mlx-audio>, MIRAGE_CLONE_REPO=<mlx-community/chatterbox-4bit>"""
import asyncio
import json
import os
import queue
import struct
import subprocess
import threading
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKER = Path(__file__).with_name("clone_worker.py")
SAMPLE_RATE = 24000


class CloneUnavailable(RuntimeError):
    pass


def sidecar_python() -> str | None:
    p = os.environ.get("MIRAGE_CLONE_PYTHON") or str(REPO_ROOT / ".venv-clone" / "bin" / "python")
    return p if Path(p).exists() else None


class CloneSidecar:
    sample_rate = SAMPLE_RATE

    def __init__(self, python: str | None = None, worker: Path | str | None = None, start_timeout: float = 240.0):
        self.python = python or sidecar_python()
        self.worker = str(worker or WORKER)
        if not self.python:
            raise CloneUnavailable("voice-clone sidecar python not found (run backend/scripts_setup_clone.sh)")
        self.start_timeout = start_timeout
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()  # start/stop
        self._wlock = threading.Lock()  # stdin writes
        self._queues: dict[int, queue.Queue] = {}
        self._next = 1

    # ---- lifecycle ----
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> None:
        with self._lock:
            if self.alive():
                return
            self._proc = subprocess.Popen([self.python, self.worker], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                          stderr=subprocess.DEVNULL, bufsize=0)
            ready = threading.Event()
            threading.Thread(target=self._read, args=(self._proc, ready), daemon=True).start()
            if not ready.wait(self.start_timeout) or not self.alive():
                self._kill()
                raise CloneUnavailable("voice-clone sidecar failed to start")

    def _kill(self) -> None:
        p, self._proc = self._proc, None
        if p and p.poll() is None:
            try:
                p.stdin.close()
                p.wait(3)
            except Exception:  # noqa: BLE001
                p.kill()

    def close(self) -> None:
        with self._lock:
            self._kill()

    def _read(self, proc: subprocess.Popen, ready: threading.Event) -> None:
        out = proc.stdout

        def exactly(n: int) -> bytes:
            buf = b""
            while len(buf) < n:
                c = out.read(n - len(buf))
                if not c:
                    raise EOFError
                buf += c
            return buf

        try:
            while True:
                rid, kind, n = struct.unpack("<III", exactly(12))
                payload = exactly(n) if n else b""
                if kind == 3:
                    ready.set()
                    continue
                q = self._queues.get(rid)
                if q is not None:
                    q.put((kind, payload))
        except (EOFError, OSError, ValueError):
            pass
        finally:  # sidecar gone: fail everything in flight
            for q in list(self._queues.values()):
                q.put((2, b"sidecar exited"))

    # ---- requests ----
    def _send(self, obj: dict) -> None:
        with self._wlock:
            self._proc.stdin.write((json.dumps(obj) + "\n").encode())
            self._proc.stdin.flush()

    def _open(self, obj: dict) -> tuple[int, queue.Queue]:
        self.start()
        with self._lock:
            rid = self._next
            self._next += 1
        q: queue.Queue = queue.Queue()
        self._queues[rid] = q
        try:
            self._send({**obj, "id": rid})
        except Exception:
            self._queues.pop(rid, None)
            raise CloneUnavailable("voice-clone sidecar not reachable")
        return rid, q

    def stream(self, text: str, ref: str | Path, lang: str = "en"):
        """Blocking generator of int16 24 kHz PCM chunks. Raises CloneUnavailable on sidecar errors."""
        rid, q = self._open({"op": "tts", "text": text, "ref": str(ref), "lang": lang})
        done = False
        try:
            while True:
                kind, payload = q.get()
                if kind == 0:
                    yield payload
                elif kind == 1:
                    done = True
                    return
                else:
                    done = True
                    raise CloneUnavailable(payload.decode(errors="replace"))
        finally:
            self._queues.pop(rid, None)
            if not done:  # consumer stopped early (barge-in): tell the sidecar to drop it
                try:
                    self._send({"op": "cancel", "id": rid})
                except Exception:  # noqa: BLE001
                    pass

    def synth(self, text: str, ref: str | Path, lang: str = "en") -> bytes:
        return b"".join(self.stream(text, ref, lang))

    def prepare(self, ref: str | Path) -> None:
        rid, q = self._open({"op": "prep", "ref": str(ref)})
        try:
            while True:
                kind, payload = q.get()
                if kind == 1:
                    return
                if kind == 2:
                    raise CloneUnavailable(payload.decode(errors="replace"))
        finally:
            self._queues.pop(rid, None)

    async def astream(self, text: str, ref: str | Path, lang: str = "en"):
        """Async generator for the live path. Cancelling it (barge-in) cancels the sidecar request."""
        gen = self.stream(text, ref, lang)
        sentinel = object()
        try:
            while True:
                chunk = await asyncio.to_thread(next, gen, sentinel)
                if chunk is sentinel:
                    return
                yield chunk
        finally:
            await asyncio.to_thread(gen.close)


_default: CloneSidecar | None = None
_dlock = threading.Lock()


def default_sidecar() -> CloneSidecar:
    """Process-wide sidecar (one model in memory per host process: API server or worker)."""
    global _default
    with _dlock:
        if _default is None:
            _default = CloneSidecar()
        return _default


def set_default_sidecar(s: CloneSidecar | None) -> None:
    global _default
    _default = s
