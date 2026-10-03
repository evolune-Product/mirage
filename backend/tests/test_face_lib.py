"""Runs workers/face_checks.py (facelib / face_render unit checks) with a workers python that has numpy+opencv."""
import subprocess
from pathlib import Path

import pytest

W = Path(__file__).resolve().parents[2] / "workers"


def _py():
    for p in (W / ".venv" / "bin" / "python", W / ".venv-face" / "bin" / "python"):
        if p.exists() and subprocess.run([str(p), "-c", "import cv2, numpy"], capture_output=True).returncode == 0:
            return str(p)
    return None


def test_facelib_checks():
    py = _py()
    if not py:
        pytest.skip("no workers venv with opencv")
    r = subprocess.run([py, str(W / "face_checks.py")], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "checks passed" in r.stdout
