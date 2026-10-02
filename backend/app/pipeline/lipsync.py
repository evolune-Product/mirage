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

    async def render(self, pcm24k: bytes) -> dict:
        """24 kHz mono int16 PCM -> {'fps', 'frames': [b64 jpeg], 'ms'}"""
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(f"{self.url}/render/{self.rid}", content=pcm24k)
            r.raise_for_status()
            return r.json()
