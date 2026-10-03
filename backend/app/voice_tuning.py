"""Interruption sensitivity / turn patience -> Session knobs."""
from __future__ import annotations


def barge_frames(base: int, sensitivity: float) -> int:
    """5 frames (~100 ms) at 0.5. 0 -> 1.6x as many, 1 -> 0.4x (never below 2)."""
    return max(2, round(base * (1.6 - 1.2 * min(max(sensitivity, 0.0), 1.0))))


def apply(sess, t) -> None:
    sess.barge_frames = barge_frames(sess.barge_frames, t.interruption_sensitivity) if t.interruption_sensitivity != 0.5 else sess.barge_frames
    if not t.allow_interruptions:
        sess.barge_frames = 10 ** 9  # never reached: the user cannot cut the agent off by voice
    sess.turn.s.end_of_turn_ms = int(t.turn_patience_ms)
