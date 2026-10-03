"""Perception ("Raven"-style): let the agent see the user's webcam / shared screen.

Client -> server over the existing conversation WebSocket (text JSON; binary stays PCM audio):
  {"type":"perception","enabled":true}                     user opts in (false = opt out, drops everything seen so far)
  {"type":"frame","source":"camera"|"screen","jpeg_b64":"<base64 JPEG>"}   every 1-3 s, or on demand
Server -> client:  {"type":"perception_status","enabled":bool,"reason":str}   and  {"type":"scene","source","text","ms"}
See docs/overnight/intelligence.md for the full contract and the privacy model.
"""
from .manager import PerceptionManager, PerceptiveLLM, attach, wants_vision  # noqa: F401
