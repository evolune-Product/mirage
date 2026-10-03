"""Lip-sync engine registry + MIRAGE_COMMERCIAL_ONLY gate (workers/engines, checks in workers/engine_checks.py).
Each case runs in its own process because the gate is read from the environment."""
import os
import subprocess
from pathlib import Path

import pytest

W = Path(__file__).resolve().parents[2] / "workers"


def _py():
    for p in (W / ".venv-face" / "bin" / "python", W / ".venv" / "bin" / "python"):
        if p.exists() and subprocess.run([str(p), "-c", "import cv2, numpy, scipy"], capture_output=True).returncode == 0:
            return str(p)
    return None


def _run(case, **env):
    py = _py()
    if not py:
        pytest.skip("no workers venv with opencv+scipy")
    e = {k: v for k, v in os.environ.items() if not k.startswith("MIRAGE_")}
    e.update(env)
    r = subprocess.run([py, str(W / "engine_checks.py"), case], capture_output=True, text=True, timeout=180, env=e, cwd=str(W))
    assert r.returncode == 0 and f"{case} ok" in r.stdout, r.stdout[-1500:] + r.stderr[-2500:]


def test_commercial_only_refuses_research_engines_and_scripts():
    _run("gate_strict", MIRAGE_COMMERCIAL_ONLY="1")


def test_unclear_weights_need_explicit_opt_in_and_never_unlock_research():
    _run("gate_unclear", MIRAGE_COMMERCIAL_ONLY="1", MIRAGE_COMMERCIAL_ALLOW_UNCLEAR="1")


def test_dev_mode_selection_and_licence_logic():
    _run("dev_auto")


def test_viseme_timeline_from_audio():
    _run("viseme")


def test_viseme_timeline_from_phonemes():
    _run("phonemes")


def test_viseme_warp_and_engine_generate():
    _run("warp")


def test_service_health_in_commercial_mode():
    _run("server_commercial", MIRAGE_COMMERCIAL_ONLY="1")


def test_service_refuses_explicit_research_engine():
    _run("server_commercial", MIRAGE_COMMERCIAL_ONLY="1", MIRAGE_LIPSYNC_ENGINE="wav2lip")


def test_service_reports_viseme_engine():
    _run("server_dev_viseme", MIRAGE_LIPSYNC_ENGINE="viseme")
