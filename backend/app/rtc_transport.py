"""Transport helpers for the browser real-time WebSocket (routers/realtime.py): client capability negotiation, binary
video framing and adaptive JPEG quality. Everything here is additive: a client that never sends `hello` gets exactly the
original protocol (raw PCM binary frames, base64 JPEG inside JSON).

Binary framing (only after the client sent {"type":"hello","framing":"tagged"}): every server->client binary message
starts with one tag byte
    0x01  agent audio: int16 24 kHz mono PCM follows
    0x02  video: u32 big-endian header length, JSON header, then the JPEG frames back to back.
          header = {"t":"video_segment"|"idle_loop","fps":25,"sizes":[n0,n1,...],"end_phase":k?}
"""
from __future__ import annotations

import base64
import io
import json
import os
import struct
import time
from dataclasses import dataclass, field

TAG_AUDIO, TAG_VIDEO = 1, 2

# JPEG tiers: (quality, scale). Tier 0 forwards the lip-sync service's JPEGs untouched.
TIERS = [(None, 1.0), (55, 1.0), (50, 0.75), (42, 0.6)]  # measured on 576x324 lip-sync frames: 32 / 17.5 / 10.3 / 6.5 KB
DEFAULT_TIER = int(os.environ.get("MIRAGE_VIDEO_TIER", "1"))
FPS_ASSUMED = 25.0
HEARTBEAT_S = 5.0
DEAD_AFTER_S = 20.0


def b64_frames(frames: list[str]) -> list[bytes]:
    return [base64.b64decode(f) for f in frames]


def recode(jpegs: list[bytes], tier: int) -> list[bytes]:
    """Re-encode JPEG frames for a lower tier (PIL; ~1-2 ms per 576x324 frame). Tier 0 or any failure => unchanged."""
    q, scale = TIERS[max(0, min(tier, len(TIERS) - 1))]
    if q is None:
        return jpegs
    try:
        from PIL import Image
    except Exception:  # noqa: BLE001
        return jpegs
    out = []
    for b in jpegs:
        try:
            im = Image.open(io.BytesIO(b)).convert("RGB")
            if scale != 1.0:
                im = im.resize((max(2, int(im.width * scale) // 2 * 2), max(2, int(im.height * scale) // 2 * 2)), Image.BILINEAR)
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=q, optimize=False, subsampling=2)  # 4:2:0 chroma
            out.append(buf.getvalue())
        except Exception:  # noqa: BLE001
            out.append(b)
    return out


def pack_video(kind: str, fps: float, jpegs: list[bytes], **extra) -> bytes:
    head = {"t": kind, "fps": fps, "sizes": [len(j) for j in jpegs], **{k: v for k, v in extra.items() if v is not None}}
    h = json.dumps(head, separators=(",", ":")).encode()
    return bytes([TAG_VIDEO]) + struct.pack(">I", len(h)) + h + b"".join(jpegs)


def unpack_video(blob: bytes) -> tuple[dict, list[bytes]]:
    """Inverse of pack_video (used by tests and the Python probe)."""
    assert blob[0] == TAG_VIDEO
    (n,) = struct.unpack(">I", blob[1:5])
    head = json.loads(blob[5:5 + n])
    off, frames = 5 + n, []
    for s in head["sizes"]:
        frames.append(blob[off:off + s])
        off += s
    return head, frames


from .wsguard import MsgGuard  # noqa: E402


@dataclass
class Link:
    """Per-socket negotiated state."""
    hello: bool = False
    tagged: bool = False
    want_tagged: bool = False
    busy: bool = False  # pump is mid-message
    tier: int = DEFAULT_TIER
    max_tier: int = len(TIERS) - 1
    idle_cached: bool = False
    resume: bool = False
    ended: bool = False  # the client said goodbye (vs. the socket just dropping)
    client: dict = field(default_factory=dict)
    last_rx: float = field(default_factory=time.monotonic)
    guard: MsgGuard = field(default_factory=MsgGuard)  # per-connection message/byte budget (wsguard.py)
    bytes_video: int = 0
    bytes_audio: int = 0
    # adaptation inputs
    bw_bps: float = 0.0  # EWMA of measured send throughput for video messages
    lag_ms: float = 0.0  # client-reported: how far behind its audio clock video segments arrive/decode
    good: int = 0  # consecutive healthy stats since the last downgrade

    def apply_hello(self, m: dict) -> None:
        self.hello = True
        self.want_tagged = m.get("framing") == "tagged"  # becomes `tagged` once hello_ack is on the wire
        self.idle_cached = bool(m.get("idle_cached"))
        self.resume = bool(m.get("resume"))
        self.client = {k: m.get(k) for k in ("ua", "audio", "sample_rates", "mobile") if m.get(k) is not None}
        if m.get("tier") is not None:
            self.tier = max(0, min(int(m["tier"]), len(TIERS) - 1))
        if m.get("max_tier") is not None:
            self.max_tier = max(0, min(int(m["max_tier"]), len(TIERS) - 1))

    def note_send(self, nbytes: int, seconds: float) -> None:
        """Throughput sample from awaiting a video send (only meaningful for sizeable messages)."""
        if nbytes < 20000 or seconds <= 0:
            return
        bps = nbytes * 8 / seconds
        self.bw_bps = bps if not self.bw_bps else 0.7 * self.bw_bps + 0.3 * bps

    def note_stats(self, lag_ms: float) -> int:
        """Client reported its video lag. Returns the (possibly changed) tier. Degrades quickly, recovers slowly."""
        self.lag_ms = lag_ms
        if lag_ms > 350 or self._bw_starved():
            self.good = 0
            if self.tier < self.max_tier:
                self.tier += 1
        else:
            self.good += 1
            if self.good >= 8 and self.tier > DEFAULT_TIER and not self._bw_marginal():  # ~16 s of stats at 2 s each
                self.tier -= 1
                self.good = 0
        return self.tier

    def _video_bps(self) -> float:
        """Rough bitrate of the current tier at 25 fps (bytes/frame measured on 576x324 lip-sync output)."""
        per_frame = [32000, 17500, 10300, 6500][max(0, min(self.tier, 3))]
        return per_frame * 8 * FPS_ASSUMED

    def _bw_starved(self) -> bool:
        return bool(self.bw_bps) and self._video_bps() > 0.6 * self.bw_bps

    def _bw_marginal(self) -> bool:
        return bool(self.bw_bps) and self._video_bps() * 1.6 > 0.6 * self.bw_bps
