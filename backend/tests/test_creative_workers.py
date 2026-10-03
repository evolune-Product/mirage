"""Runs workers/creative_checks.py (background, captions, formats, scenes) and checks the backend option lists match the workers."""
import subprocess
from pathlib import Path

import pytest

W = Path(__file__).resolve().parents[2] / "workers"


def _py():
    for p in (W / ".venv-face" / "bin" / "python", W / ".venv" / "bin" / "python"):
        if p.exists() and subprocess.run([str(p), "-c", "import cv2, numpy, PIL, scipy, mediapipe"], capture_output=True).returncode == 0:
            return str(p)
    return None


def test_creative_checks():
    py = _py()
    if not py:
        pytest.skip("no workers venv with opencv/mediapipe")
    r = subprocess.run([py, str(W / "creative_checks.py")], capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "creative checks passed" in r.stdout


def test_option_lists_match_workers():
    py = _py()
    if not py:
        pytest.skip("no workers venv")
    code = ("import sys,json; sys.path.insert(0,%r); import formats as f, captions as c, background as b;"
            "print(json.dumps([list(f.ASPECTS),list(f.RESOLUTIONS),list(f.TRANSITIONS),list(c.STYLES),list(f.POSITIONS)]))" % str(W))
    r = subprocess.run([py, "-c", code], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    import json
    from app import creative_options as co

    assert json.loads(r.stdout.strip().splitlines()[-1]) == [list(co.ASPECTS), list(co.RESOLUTIONS), list(co.TRANSITIONS),
                                                            list(co.CAPTION_STYLES), list(co.LOGO_POSITIONS)]
