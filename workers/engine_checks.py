"""Plain-assert checks for workers/engines (run by backend/tests/test_engines.py in a workers venv with numpy+opencv+scipy).

  python engine_checks.py <case>      cases: gate_strict gate_unclear dev_auto viseme phonemes warp server_commercial server_dev_viseme
Env is set by the caller (VOCALFACE_COMMERCIAL_ONLY etc.), so each case is its own process."""
import os
import subprocess
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import engines  # noqa: E402
from engines import CommercialOnlyError, LicenceInfo, Weight  # noqa: E402


def gate_strict():
    assert engines.commercial_only()
    for call in (lambda: engines.check_allowed("wav2lip"), lambda: engines.resolve("wav2lip"), lambda: engines.create("wav2lip", "cpu")):
        try:
            call()
        except CommercialOnlyError as e:
            assert "wav2lip" in str(e) and "VOCALFACE_COMMERCIAL_ONLY" in str(e)
        else:
            raise AssertionError("wav2lip must be refused in commercial-only mode")
    engines.check_allowed("viseme")
    st = engines.status()
    assert st["commercial_only"] and "wav2lip" in st["disabled"] and "viseme" not in st["disabled"], st["disabled"]
    # musetalk unet provenance is 'unclear' -> refused by default (needs explicit opt-in)
    assert "musetalk" in st["disabled"]
    assert engines.resolve() == "viseme"  # auto
    assert st["engines"]["wav2lip"]["licence"]["commercial"] is False
    assert st["engines"]["viseme"]["licence"]["commercial"] is True
    try:
        engines.require_commercial_safe("liveportrait")
    except CommercialOnlyError:
        pass
    else:
        raise AssertionError("liveportrait must be blocked")
    # worker scripts refuse too (separate processes, same env)
    r = subprocess.run([sys.executable, str(HERE / "photo_idle.py"), "--image", "x.png", "--out", "y.mp4"], capture_output=True, text=True)
    assert r.returncode != 0 and "refused" in (r.stderr + r.stdout), r.stderr
    r = subprocess.run([sys.executable, str(HERE / "render_offline.py"), "--source", "a", "--audio", "b", "--out", "c", "--engine", "wav2lip"],
                       capture_output=True, text=True)
    assert r.returncode != 0 and "refused" in (r.stderr + r.stdout), r.stderr


def gate_unclear():
    assert engines.commercial_only() and os.environ.get("VOCALFACE_COMMERCIAL_ALLOW_UNCLEAR") == "1"
    engines.check_allowed("musetalk")  # operator opted in
    try:
        engines.check_allowed("wav2lip")
    except CommercialOnlyError:
        pass
    else:
        raise AssertionError("opt-in must not unlock research-only engines")


def dev_auto():
    assert not engines.commercial_only()
    engines.check_allowed("wav2lip")  # allowed outside commercial mode
    assert engines.resolve() in ("wav2lip", "viseme")
    assert engines.status()["disabled"] == []
    lic = LicenceInfo("x", weights=(Weight("a", "mit", True), Weight("b", "?", None)))
    assert lic.commercial is None
    assert LicenceInfo("x", weights=(Weight("a", "mit", True),)).commercial is True
    assert LicenceInfo("x", weights=(Weight("a", "nc", False),)).commercial is False
    assert LicenceInfo("x", research_only=True).commercial is False


def viseme():
    from engines import viseme as V

    sr = 16000
    t = np.arange(sr * 2) / sr
    silence = np.zeros(sr * 2, np.float32)
    v = V.audio_visemes(silence, 50)
    assert v["open"].max() < 0.01 and v["teeth"].max() < 0.01
    # amplitude-modulated 'aa'-like tone: loud frames open the mouth, quiet frames close it
    env = (np.sin(2 * np.pi * 2 * t) > 0).astype(np.float32)
    tone = (0.2 * env * (np.sin(2 * np.pi * 700 * t) + 0.5 * np.sin(2 * np.pi * 1200 * t))).astype(np.float32)
    v = V.audio_visemes(tone, 50)
    loud = v["open"][[3, 4, 14, 15, 16]].mean(); quiet = v["open"][[9, 10, 11, 22, 23]].mean()
    assert loud > 0.3 and quiet < 0.3 and loud > 3 * quiet, (loud, quiet)
    rng = np.random.default_rng(0)
    hiss = (0.1 * rng.standard_normal(sr * 2)).astype(np.float32)
    h = V.audio_visemes(hiss, 50)
    assert h["teeth"][5:40].mean() > 0.5 and h["open"][5:40].mean() < 0.6 * loud + 0.2  # sibilants: teeth, small opening
    low = V.audio_visemes((0.2 * np.sin(2 * np.pi * 350 * t)).astype(np.float32), 50)
    high = V.audio_visemes((0.2 * np.sin(2 * np.pi * 2200 * t)).astype(np.float32), 50)
    assert high["width"][10:40].mean() > low["width"][10:40].mean()  # rounded vs spread
    assert all(len(x) == 50 for x in v.values())


def phonemes():
    from engines import viseme as V

    tl = [("sil", 0, 0.2), ("m", 0.2, 0.3), ("aa", 0.3, 0.6), ("uw", 0.6, 0.9), ("zzz", 0.9, 1.0)]
    v = V.visemes_from_phonemes(tl, 25)
    assert v["open"][6] < 0.3 and v["open"][11] > 0.6, (v["open"][6], v["open"][11])
    assert v["width"][20] < v["width"][11]  # uw rounder than aa
    assert len(v["open"]) == 25


def _fake_face(H=200, W=200):
    """478 landmark array with a plausible mouth, on a skin-coloured image."""
    p = np.zeros((478, 2), np.float32)
    p[:] = (W / 2, H / 2)
    cx, cy, wm = 100.0, 140.0, 50.0
    from engines import viseme as V
    for k, i in enumerate(V.UPPER_OUT):
        f = k / (len(V.UPPER_OUT) - 1); p[i] = (cx - wm / 2 + f * wm, cy - 6 * np.sin(np.pi * f))
    for k, i in enumerate(V.LOWER_OUT):
        f = k / (len(V.LOWER_OUT) - 1); p[i] = (cx - wm / 2 + f * wm, cy + 8 * np.sin(np.pi * f))
    for k, i in enumerate(V.UPPER_IN):
        f = k / (len(V.UPPER_IN) - 1); p[i] = (cx - wm / 2 + 3 + f * (wm - 6), cy - 1 * np.sin(np.pi * f))
    for k, i in enumerate(V.LOWER_IN):
        f = k / (len(V.LOWER_IN) - 1); p[i] = (cx - wm / 2 + 3 + f * (wm - 6), cy + 1 * np.sin(np.pi * f))
    for j, i in enumerate(V.CHIN):
        p[i] = (cx + (j - 3.5) * 5, cy + 40)
    img = np.full((H, W, 3), (130, 150, 200), np.uint8)
    return img, p


def warp():
    from engines import viseme as V

    img, p = _fake_face()
    out, poly = V.warp_mouth(img, p, 0.0, 1.0)
    assert np.abs(out.astype(int) - img.astype(int)).max() == 0 or np.abs(out.astype(int) - img.astype(int)).mean() < 0.5
    out, poly = V.warp_mouth(img, p, 0.8, 1.0)
    assert out.shape == img.shape
    # lower inner lip moved down, upper inner lip up (or equal)
    base_poly = p[V.UPPER_IN + V.LOWER_IN[::-1][1:-1]]
    h0 = base_poly[:, 1].max() - base_poly[:, 1].min(); h1 = poly[:, 1].max() - poly[:, 1].min()
    assert h1 > h0 + 5, (h0, h1)
    V.paint_interior(out, poly, img, base_poly, 0.8, 0.8)
    assert (out != img).any()
    # far from the mouth nothing changes (warp is local)
    assert np.abs(out[:40].astype(int) - img[:40].astype(int)).max() == 0
    # engine.generate with a Base-like object; zero params pass the base crop through
    class B:  # minimal Base
        frames = [img]; pts = [p]; boxes = np.array([[0, 0, 200, 200]]); masks = [np.ones((200, 200), np.float32)]
    e = V.VisemeEngine().load("cpu")
    o = e.generate(B, [0, 0], {"open": [0.0, 0.7], "width": [1.0, 1.0], "teeth": [0.0, 0.5]})
    assert (o[0] == img).all() and (o[1] != img).any()


def _server_env_ok():
    os.environ.setdefault("VOCALFACE_DATA", "/tmp/_engine_checks_data")
    os.environ["VOCALFACE_LIPSYNC_PRELOAD"] = "0"


def server_commercial():
    _server_env_ok()
    from fastapi.testclient import TestClient
    import lipsync_server as ls

    c = TestClient(ls.app)
    h = c.get("/health").json()
    assert h["commercial_only"] is True and "wav2lip" in h["disabled_engines"], h
    explicit = os.environ.get("VOCALFACE_LIPSYNC_ENGINE") == "wav2lip"
    if not explicit:
        assert h["engine"] in ("viseme", "musetalk"), h["engine"]
        assert h["engine_licence"]["commercial"] in (True, None)
    assert h["engines"]["wav2lip"]["disabled_by_policy"]
    if explicit:  # explicitly requested research engine -> refused, service says why
        assert h["engine_error"] and "VOCALFACE_COMMERCIAL_ONLY" in h["engine_error"]
        r = c.post("/render/x", content=b"\0" * 8000)
        assert r.status_code == 503, r.status_code


def server_dev_viseme():
    _server_env_ok()
    from fastapi.testclient import TestClient
    import lipsync_server as ls

    h = TestClient(ls.app).get("/health").json()
    assert h["engine"] == "viseme" and h["commercial_only"] is False and h["engine_error"] is None
    assert h["engine_licence"]["commercial"] is True


if __name__ == "__main__":
    globals()[sys.argv[1]]()
    print(f"{sys.argv[1]} ok")
