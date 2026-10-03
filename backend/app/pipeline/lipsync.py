"""Client for the live lip-sync service (workers/lipsync_server.py, runs in workers/.venv on Apple MPS or a GPU box)."""
import os

import httpx

URL = os.environ.get("MIRAGE_LIPSYNC_URL", "http://localhost:8100")


class LipsyncClient:
    def __init__(self, replica_id: str, url: str = URL):
        self.rid, self.url = replica_id, url.rstrip("/")

    @staticmethod
    async def available(url: str = URL) -> bool:
        try:
            async with httpx.AsyncClient(timeout=1.5) as c:
                return (await c.get(url.rstrip("/") + "/health")).status_code == 200
        except Exception:
            return False

    async def idle(self) -> dict:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(f"{self.url}/idle/{self.rid}")
            r.raise_for_status()
            return r.json()

    async def prepare(self) -> dict:
        """Warm the replica's processed base clip (face tracking + overlay crop, ~5-10 s cold, instant when cached).
        Call when a replica becomes ready / a conversation is created, so the first /idle is fast."""
        async with httpx.AsyncClient(timeout=120) as c:
            r = await c.post(f"{self.url}/prepare/{self.rid}")
            r.raise_for_status()
            return r.json()

    async def render(self, pcm24k: bytes, phase: int | None = None, fade_in: bool = False) -> dict:
        """24 kHz mono int16 PCM -> {'fps', 'frames': [b64 jpeg], 'ms', 'start_phase', 'end_phase', 'loop_len'}

        phase: ping-pong position of the idle loop to continue from (so the head does not jump when speech starts);
        fade_in: force a soft mouth entry even if the previous piece ended just now."""
        params = {}
        if phase is not None:
            params["phase"] = int(phase)
        if fade_in:
            params["fade_in"] = 1
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(f"{self.url}/render/{self.rid}", content=pcm24k, params=params)
            r.raise_for_status()
            return r.json()

    async def health(self) -> dict:
        async with httpx.AsyncClient(timeout=3) as c:
            r = await c.get(f"{self.url}/health")
            r.raise_for_status()
            return r.json()
