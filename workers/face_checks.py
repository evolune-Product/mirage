"""Plain-assert unit checks for facelib/face_render (no GPU, no mediapipe, no models). Run with any python that has
numpy+opencv:  python face_checks.py   (exit code != 0 on failure). backend/tests/test_face_lib.py runs it."""
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import face_render as fr  # noqa: E402
import facelib as fl  # noqa: E402

rng = np.random.default_rng(0)


def scene(n=40, label=True, W=320, H=180):
    frames = []
    for i in range(n):
        f = np.full((H, W, 3), 120, np.uint8)
        f = (f + rng.integers(-25, 25, f.shape)).clip(0, 255).astype(np.uint8)  # moving "background"
        cv2.circle(f, (160 + int(5 * np.sin(i / 3)), 80), 30, (150, 160, 200), -1)  # the "face"
        if label:
            f[H - 14:H, 0:60] = 5
            cv2.putText(f, "Jane Doe", (2, H - 3), cv2.FONT_HERSHEY_PLAIN, 0.8, (255, 255, 255), 1)
        frames.append(f)
    return frames


def test_smooth_series():
    x = np.cumsum(rng.normal(size=200))[:, None] * 0 + rng.normal(size=(200, 2))
    y = fl.smooth_series(x, 3.0)
    assert y.shape == x.shape and y.std() < 0.5 * x.std()
    z = x.copy(); z[10:15] = np.nan
    assert np.isfinite(fl.smooth_series(z, 2.0)).all()
    assert np.allclose(fl.smooth_series(np.ones((5, 1)), 2.0), 1.0)


def test_overlay_detection():
    ov = fl.detect_overlays(scene(label=True))
    assert len(ov) == 1, ov
    x0, y0, x1, y1 = ov[0]
    assert x0 <= 2 and y1 >= 178 and 40 <= x1 <= 80
    assert fl.detect_overlays(scene(label=False)) == []
    assert fl.detect_overlays(scene(n=3)) == []


def test_crop_avoids_overlay_and_keeps_face():
    boxes = np.array([[130, 50, 60, 70.0]] * 10)
    ov = [(0, 166, 60, 180)]
    c = fl.choose_crop(320, 180, boxes, ov)
    assert not fl.rect_overlap(c, ov[0]), c
    assert c[0] <= 130 and c[2] >= 190 and c[1] <= 50 and c[3] >= 120
    assert (c[2] - c[0]) % 2 == 0 and (c[3] - c[1]) % 2 == 0
    c2 = fl.choose_crop(320, 180, boxes, [])
    assert 0 <= c2[0] < c2[2] <= 320 and 0 <= c2[1] < c2[3] <= 180


def test_calm_window():
    jaw = np.full(300, 0.3); jaw[120:240] = 0.02
    s = fl.calm_window(jaw, np.zeros(300), np.zeros((300, 2)), win=60)
    assert 118 <= s <= 182, s
    blink = np.zeros(300); blink[130:140] = 1.0
    s2 = fl.calm_window(jaw, blink, np.zeros((300, 2)), win=60)
    assert not (s2 <= 139 and s2 + 60 > 130)  # avoids the blink while staying in the closed-mouth region


def fake_pts(cx=100.0, scale=1.0):
    p = np.zeros((478, 2), np.float32) + [cx, 100]
    p[234] = [cx - 50 * scale, 100]; p[454] = [cx + 50 * scale, 100]
    p[2] = [cx, 110]; p[152] = [cx, 150]; p[6] = [cx, 80]; p[195] = [cx, 95]
    for k, i in enumerate(fl.FACE_OVAL):
        a = 2 * np.pi * k / len(fl.FACE_OVAL)
        p[i] = [cx + 55 * scale * np.cos(a), 105 + 55 * scale * np.sin(a)]
    for k, i in enumerate(fl.LIPS_OUTER):
        a = 2 * np.pi * k / len(fl.LIPS_OUTER)
        p[i] = [cx + 15 * np.cos(a), 128 + 6 * np.sin(a)]
    return p


def test_mask_and_composite():
    pts = fake_pts()
    m = fl.mouth_mask((200, 200), pts)
    assert m.shape == (200, 200) and m.dtype == np.float32 and 0 <= m.min() and m.max() <= 1.0
    assert m[128, 100] > 0.9 and m[10, 10] == 0 and m[190, 190] == 0
    m2 = fl.mouth_mask((100, 100), None, (10, 10, 80, 80))
    assert m2.max() > 0.9
    frame = np.full((200, 200, 3), 100, np.uint8)
    gen = np.full((100, 100, 3), 180, np.uint8)
    out = fl.composite_mouth(frame, gen, (50, 60, 150, 160), m[60:160, 50:150], sharpen=0.6)
    assert out.shape == frame.shape and out.dtype == np.uint8
    assert (out[:50] == frame[:50]).all()  # outside the crop untouched
    # colour matching makes the pasted patch follow the surrounding tone instead of the generator's
    assert abs(int(out[128, 100].mean()) - 100) < 40
    flat = np.full((20, 20, 3), 90, np.uint8)
    assert (fl.guarded_sharpen(flat) == flat).all()


def test_model_boxes_fixed_size_and_bounds():
    pts = np.stack([fake_pts(cx=100 + 8 * np.sin(i / 4)) for i in range(60)])
    b = fr.model_boxes(pts, 300, 400)
    sizes = {(int(r[2] - r[0]), int(r[3] - r[1])) for r in b}
    assert len(sizes) == 1, sizes
    assert (b[:, 0] >= 0).all() and (b[:, 2] <= 400).all() and (b[:, 1] >= 0).all() and (b[:, 3] <= 300).all()
    jitter_in = np.abs(np.diff(pts[:, 2, 0])).mean()  # fake nose x is constant; use cheeks for motion
    cx = (pts[:, 234, 0] + pts[:, 454, 0]) / 2
    bx = (b[:, 0] + b[:, 2]) / 2
    assert np.corrcoef(cx, bx)[0, 1] > 0.9  # follows the head
    assert np.abs(np.diff(bx)).max() <= np.abs(np.diff(cx)).max() + 1.0  # but is smoothed


def test_pingpong_continuity():
    class B:  # minimal stand-in
        frames = [0] * 5
        cursor = 0
    b = B()
    s1 = fr.pingpong_seq(b, 7)
    s2 = fr.pingpong_seq(b, 7)
    s = s1 + s2
    assert all(0 <= v < 5 for v in s)
    assert all(abs(s[i + 1] - s[i]) <= 1 for i in range(len(s) - 1)), s  # never jumps: no pops at request boundaries
    assert fr.pingpong_seq(b, 4, advance=False, start=3) == [3, 4, 3, 2]


def test_content_rect_trims_black_bars():
    fr_ = [np.full((100, 160, 3), 120, np.uint8) for _ in range(12)]
    for f in fr_:
        f[80:] = 4          # bottom letterbox
        f[:, :6] = 3        # left window edge
    x0, y0, x1, y1 = fl.content_rect(fr_)
    assert y1 <= 80 and y1 >= 76 and x0 >= 6 and x0 <= 10 and y0 <= 2 and x1 >= 158, (x0, y0, x1, y1)
    assert fl.content_rect([np.full((50, 50, 3), 100, np.uint8)] * 10) == (0, 0, 50, 50)


def test_speech_alpha():
    t = np.arange(16000 * 2) / 16000
    a = np.where(t < 1.0, 0.0, 0.2 * np.sin(2 * np.pi * 180 * t)).astype(np.float32)  # 1 s silence, 1 s "speech"
    al = fr.speech_alpha(a, 50)
    assert al[:20].max() < 0.05 and al[30:].min() > 0.95, (al[:20].max(), al[30:].min())
    assert (np.abs(np.diff(al)) < 0.7).all()  # no single-frame full flips except the attack
    assert fr.speech_alpha(np.zeros(1600, np.float32), 3).max() == 0


if __name__ == "__main__":
    n = 0
    for k, v in list(globals().items()):
        if k.startswith("test_"):
            v(); n += 1; print("ok", k)
    print(f"{n} checks passed")
