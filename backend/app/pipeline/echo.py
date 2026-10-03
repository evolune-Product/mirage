"""Speaker-echo detection against the audio the server itself sent to the client (a reference-based, envelope-domain
echo test; it is NOT an echo canceller - the browser's AEC does the cancelling, this decides what to do with what leaks).

Why it works: when the agent's voice leaks from the speaker into the microphone, the loudness envelope of the mic
follows the loudness envelope of the agent audio we sent, delayed by playback + acoustic + network latency. A user
talking over the agent does not follow it (and is usually louder than the echo that the same agent frame explains).

Time is counted in 20 ms microphone frames (the same clock the mic audio arrives on), so the logic is deterministic and
works identically for real-time and for faster-than-real-time simulation.
"""
from __future__ import annotations

import os

import numpy as np

FRAME_MS = 20
MAX_LAG = int(os.environ.get("MIRAGE_ECHO_MAX_LAG_FRAMES", "100"))  # 2.0 s: playback buffering + acoustic + network
WINDOW = 25  # 500 ms of envelope compared per decision
WARMUP = int(os.environ.get("MIRAGE_ECHO_WARMUP_FRAMES", "45"))  # frames after a reply starts in which no barge-in is accepted
MIN_WINDOW = 6  # shortest history we will judge on (start of a call/reply); needs a higher correlation
CORR_ON = float(os.environ.get("MIRAGE_ECHO_CORR", "0.6"))  # envelope correlation that counts as 'follows the agent'
RATIO = float(os.environ.get("MIRAGE_ECHO_RATIO", "2.0"))  # mic louder than 2x the predicted echo => the user is on top
SUSPECT_GAIN = 0.1  # echo weaker than about -20 dB is harmless; only louder echo raises the 'use headphones' hint
FLOOR = 30.0  # rms below this is silence/noise-floor, never evidence of anything
ENABLED = os.environ.get("MIRAGE_ECHO_REF", "1") != "0"


def _env(x: np.ndarray, frame: int) -> np.ndarray:
    n = len(x) // frame
    if n == 0:
        return np.zeros(0, dtype=np.float32)
    return np.sqrt((x[: n * frame].reshape(n, frame) ** 2).mean(axis=1))


def _log(e: np.ndarray) -> np.ndarray:
    return np.log1p(e / 50.0)


class EchoReference:
    def __init__(self):
        self.m = -1  # index of the latest mic frame
        self.mic: list[float] = []  # mic rms per frame (grows with the call; trimmed)
        self.agent = np.zeros(0, dtype=np.float32)  # rms per 20 ms of agent audio, playback order
        self.start = 0  # mic-frame index at which agent[0] starts playing (before latency)
        self.score = 0.0  # last best envelope correlation
        self.lag = 0
        self.gain = 0.0
        self.suspect_frames = 0  # agent-voiced mic frames judged to be echo
        self.voiced_frames = 0  # frames where the agent was audible (denominator for the echo fraction)
        self._trim_at = 0
        self._lag_ema = 0
        self._g_ema = 0.0  # slow estimate of the speaker->mic gain, updated only on clean echo

    # ---- reference side ----
    def add_agent(self, pcm24k: bytes) -> None:
        """Called for every agent audio chunk as it is sent (24 kHz mono int16)."""
        if not pcm24k:
            return
        x = np.frombuffer(pcm24k[: len(pcm24k) // 2 * 2], dtype=np.int16).astype(np.float32)
        e = _env(x, 24000 * FRAME_MS // 1000)
        now = max(self.m, 0)
        end = self.start + len(self.agent)  # mic index where already-queued agent audio finishes playing
        if len(self.agent) == 0 or now > end + 2:  # the previous reply has finished: this is a new playback timeline
            self.agent = e.copy()
            self.start = now
        else:  # the client plays queued chunks back to back
            self.agent = np.concatenate([self.agent, e])

    # ---- mic side ----
    def push_mic(self, rms: float) -> None:
        self.m += 1
        self.mic.append(float(rms))
        if len(self.mic) > 4000:  # 80 s of history is plenty
            del self.mic[:2000]
            self._trim_at += 2000

    def _mic_at(self, i: int) -> float:
        k = i - self._trim_at
        return self.mic[k] if 0 <= k < len(self.mic) else 0.0

    def warming(self) -> bool:
        """First moments of a reply: the echo has not reached the mic yet (up to ~0.6 s of playback+acoustic delay), so there
        is nothing to correlate. Whatever the mic hears now is far more likely the speaker than the user."""
        return len(self.agent) > 0 and 0 <= self.m - self.start < WARMUP

    def clear(self) -> None:
        self.agent = np.zeros(0, dtype=np.float32)

    def agent_audible(self, tail: int = 25) -> bool:
        """True while agent audio is (probably) still playing or its echo can still arrive (`tail` frames after the end)."""
        return len(self.agent) > 0 and self.m <= self.start + len(self.agent) + tail

    def assess(self) -> tuple[bool, float]:
        """(explained_by_echo, correlation) for the current mic frame. explained_by_echo => treat as not-the-user."""
        if not (ENABLED and self.agent_audible()):
            return False, 0.0
        m = self.m
        W = min(WINDOW, m - self._trim_at + 1)
        if W < MIN_WINDOW:
            return False, 0.0
        mic_w = np.array([self._mic_at(i) for i in range(m - W + 1, m + 1)], dtype=np.float32)
        # candidate lags: agent sample j = m - start - lag. Build every window at once.
        padded = np.concatenate([np.zeros(W + MAX_LAG, np.float32), self.agent, np.zeros(MAX_LAG + W, np.float32)])
        base = W + MAX_LAG  # padded index of agent[0]
        lags = np.arange(0, MAX_LAG + 1)
        j_end = m - self.start - lags  # agent index aligned with the newest mic frame, per lag
        idx = (j_end[:, None] - np.arange(W - 1, -1, -1)[None, :]) + base
        idx = np.clip(idx, 0, len(padded) - 1)
        aw = padded[idx]  # (nlags, W)
        active = aw.max(axis=1) > FLOOR
        if not active.any() or mic_w.max() < FLOOR:
            self.score = 0.0
            return False, 0.0
        la, lm = _log(aw), _log(mic_w)
        la_c = la - la.mean(axis=1, keepdims=True)
        lm_c = lm - lm.mean()
        den = np.sqrt((la_c ** 2).sum(axis=1) * (lm_c ** 2).sum()) + 1e-6
        corr = (la_c * lm_c[None, :]).sum(axis=1) / den
        corr[~active] = -1.0
        k = int(np.argmax(corr))
        c = float(corr[k])
        # predicted echo level now: the loudest agent frame within +-2 frames of the best lag (envelopes smear in time)
        j0 = m - self.start - int(lags[k])
        near = [self.agent[j] for j in range(j0 - 2, j0 + 3) if 0 <= j < len(self.agent)]
        a_last = float(max(near)) if near else 0.0
        # echo gain: a LOW percentile of mic/agent over the window (a user talking over the echo only adds to the mic, so
        # a mean would be inflated by exactly the speech we want to detect), plus a slow memory of the clean-echo gain
        loud = aw[k] > 10 * FLOOR
        g_win = float(np.percentile(mic_w[loud] / aw[k][loud], 30)) if loud.sum() >= 3 else float(
            (aw[k] * mic_w).sum() / ((aw[k] ** 2).sum() + 1e-6))
        gain = min(g_win, self._g_ema) if self._g_ema else g_win
        self.score, self.lag, self.gain = c, int(lags[k]), gain
        nf = float(np.percentile(np.array(self.mic[-150:], dtype=np.float32), 10))  # room noise floor
        mic_now = float(mic_w[-1])
        user_on_top = mic_now > RATIO * gain * a_last + 2.0 * nf + FLOOR
        need = CORR_ON if W >= 15 else max(CORR_ON, 0.8)
        explained = c >= need and not user_on_top
        if explained and c >= 0.85:  # clean, strongly correlated echo: learn the speaker->mic path (gain and delay)
            self._g_ema = g_win if not self._g_ema else 0.9 * self._g_ema + 0.1 * min(g_win, 2 * self._g_ema)
            self._lag_ema = int(lags[k])
        if self._g_ema:  # path known: judge by predicted-vs-observed level, no need for the window to correlate again
            j1 = m - self.start - self._lag_ema
            near = [self.agent[j] for j in range(j1 - 2, j1 + 3) if 0 <= j < len(self.agent)]
            pred = self._g_ema * (max(near) if near else 0.0)
            explained = mic_now <= RATIO * pred + 2.0 * nf + FLOOR
        self.voiced_frames += 1
        if c >= CORR_ON and float(mic_w[-1]) > FLOOR * 2 and gain >= SUSPECT_GAIN:
            self.suspect_frames += 1
        return explained, c

    @property
    def suspect_fraction(self) -> float:
        return self.suspect_frames / self.voiced_frames if self.voiced_frames else 0.0

    def reset_stats(self) -> None:
        self.suspect_frames = self.voiced_frames = 0
