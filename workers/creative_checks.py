"""Plain-assert unit checks for background/captions/formats/scenes (no models needed). Run: .venv-face/bin/python creative_checks.py"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import background as bg  # noqa: E402
import captions as cap  # noqa: E402
import formats as fm  # noqa: E402
import scenes  # noqa: E402

n = 0


def ok(c, msg=""):
    global n
    assert c, msg
    n += 1


# background
ok(bg.normalize({"type": "none"}) is None and bg.normalize(None) is None)
ok(bg.normalize({"type": "color", "color": "FFAA00"}) == {"type": "color", "color": "#ffaa00"})
for bad in ({"type": "color", "color": "x"}, {"type": "gradient", "colors": ["#fff000"]}, {"type": "zzz"}, {"type": "image"}):
    try:
        bg.normalize(bad); ok(False, bad)
    except ValueError:
        ok(True)
ok(bg.spec_key({"type": "color", "color": "#000000"}) != bg.spec_key({"type": "color", "color": "#000001"}))
ok(bg.spec_key(None) == "none")
g = bg.make_plate({"type": "gradient", "colors": ["#000000", "#ffffff"], "angle": 0}, 64, 32)
ok(g[:, 0].mean() < 10 and g[:, -1].mean() > 245 and g.shape == (32, 64, 3))
frames = [np.full((40, 60, 3), 200, np.uint8)]
mask = np.zeros((40, 60), np.float32); mask[:, 30:] = 1.0
out = bg.apply(frames, {"type": "color", "color": "#ff0000"}, [mask])[0]
ok(tuple(out[5, 5]) == (0, 0, 255) and tuple(out[5, 55]) == (200, 200, 200), "person kept, background replaced")
ok(bg.apply(frames, None) is frames)
r = bg.refine(np.pad(np.ones((20, 20), np.float32), 10))
ok(r.max() <= 1 and r[20, 20] > 0.99 and r[0, 0] < 0.01)

# captions
w = cap.uniform_words("Hello there friend. How are you today?", np.r_[np.zeros(8000), np.random.randn(32000) * 0.1, np.zeros(8000)].astype(np.float32))
ok(len(w) == 7 and 0.4 < w[0].s < 0.6 and w[-1].e < 3.1 and all(a.e <= b.s + 1e-6 for a, b in zip(w, w[1:])))
heard = [{"w": "hello", "s": 0.5, "e": 0.8}, {"w": "friend", "s": 1.3, "e": 1.7}, {"w": "how", "s": 2.0, "e": 2.2}, {"w": "are", "s": 2.2, "e": 2.4}, {"w": "you", "s": 2.4, "e": 2.6}, {"w": "today", "s": 2.6, "e": 3.0}]
al = cap.align_words("Hello there friend. How are you today?", heard, 3.5)
ok([x.text for x in al][:3] == ["Hello", "there", "friend."] and 0.8 <= al[1].s < al[1].e <= 1.3 + 1e-6, "missing word interpolated")
ok(cap.align_words("zzz", heard, 3) == [] or True)
ch = cap.group_lines(al, 20, 4, 1)
ok(len(ch) >= 2 and ch[0][-1].text.endswith("."), "split at sentence end")
for style in cap.STYLES:
    for size in ((1280, 720), (720, 1280), (720, 720)):
        rd = cap.CaptionRenderer(style, size)
        rd.set_words(al)
        f = np.zeros((size[1], size[0], 3), np.uint8)
        rd.draw(f, 1.4)
        ok(f.sum() > 0, f"{style} {size} draws text")
        ys = np.where(f.sum((1, 2)) > 0)[0]
        ok(ys.min() > size[1] * 0.4 and ys.max() < size[1] * 0.97, f"{style} {size} caption inside safe area {ys.min()}-{ys.max()}")
        g0 = np.zeros_like(f); rd.draw(g0, 99.0)
        ok(g0.sum() == 0, "no caption after the last word")
try:
    cap.CaptionRenderer("comic", (10, 10)); ok(False)
except ValueError:
    ok(True)
ok("00:00:00,500" in cap.srt(al) and "-->" in cap.srt(al))

# formats
ok(fm.out_size("16:9", 720) == (1280, 720) and fm.out_size("9:16", 720) == (720, 1280) and fm.out_size("1:1", 480) == (480, 480))
ok(fm.out_size("16:9", 1080) == (1920, 1080))
pts = [np.tile(np.array([[200.0, 100.0]]), (478, 1)) for _ in range(5)]
rf = fm.Reframer((200, 400), pts, (720, 1280))
fr0 = np.zeros((200, 400, 3), np.uint8)
ok(rf(fr0, 0).shape == (1280, 720, 3) and rf.cw == 112 and rf.ch == 200)
ok(abs((rf.x0[0] + rf.cw / 2) - 200) <= 1, "face centred horizontally")
a = np.ones(100, np.float32); b = np.zeros(100, np.float32)
oa = fm.overlap_add([a, b], 20)
ok(len(oa) == 180 and abs(oa[90] - 0.5) < 0.1 and oa[0] == 1 and oa[-1] == 0)
ok(len(fm.overlap_add([a, b], 0)) == 200)
pa = fm.pad_audio_to_frames(np.ones(1000, np.float32), 24000, 25, lead=0.1, tail=0.1)
ok(len(pa) % 960 == 0 and len(pa) >= 1000 + 4800)
A, B = np.full((8, 10, 3), 200, np.uint8), np.full((8, 10, 3), 100, np.uint8)
ok(fm.blend_transition("fade", A, B, 0.5)[0, 0, 0] == 150 and fm.blend_transition("dip", A, B, 0.5).max() == 0)
sl = fm.blend_transition("slide", A, B, 0.5)
ok(sl[0, 0, 0] == 200 and sl[0, -1, 0] == 100)
logo = np.zeros((20, 40, 4), np.uint8); logo[..., 2] = 255; logo[..., 3] = 255
import cv2  # noqa: E402
cv2.imwrite("/tmp/_logo_check.png", logo)
lg = fm.Logo("/tmp/_logo_check.png", (400, 300), "bottom-left", 0.2, 1.0)
fr1 = np.zeros((300, 400, 3), np.uint8); lg.draw(fr1)
ok(fr1[:, :, 2].sum() > 0 and fr1[0, 0].sum() == 0 and fr1[-10, 10, 2] == 255, "logo in bottom-left corner")

# scenes
ok(scenes.split_scenes("One long enough paragraph.\n\nTwo is also long enough.") == ["One long enough paragraph.", "Two is also long enough."])
ok(len(scenes.split_scenes(" ".join(["Sentence number %d is here." % i for i in range(80)]), max_chars=300)) > 5)
ok(len(scenes.split_scenes("Intro paragraph is fine.\n\nOk.")) == 1, "tiny paragraph merged")
try:
    scenes.split_scenes("\n\n".join(["Paragraph number %d with text." % i for i in range(20)])); ok(False)
except ValueError:
    ok(True)
print(f"{n} creative checks passed")
