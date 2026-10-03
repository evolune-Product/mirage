"""Creative offline renderer: Wav2Lip lip-sync on a replica's base clip + custom background, burned-in captions, aspect
ratios, logo, multi-scene transitions, thumbnail. Runs in workers/.venv-face (mediapipe + torch).

  .venv-face/bin/python creative_render.py spec.json

spec.json:
{
  "replica_dir": "/data/replicas/rep_x",          # source.mp4 and/or listening.mp4 (photo replicas: the generated idle clip)
  "out": "/data/videos/vid_x.mp4", "thumbnail": "/data/videos/vid_x.jpg"?, "srt": "/data/videos/vid_x.srt"?,
  "format": "16:9"|"9:16"|"1:1", "resolution": 720,
  "background": {"type": "color", "color": "#101828"}?,           # see background.py
  "captions": {"style": "bold", "accent": "#ffd23f"}?,
  "logo": {"path": "...", "position": "top-right", "scale": 0.14, "opacity": 0.9}?,
  "transition": "fade", "transition_s": 0.4,
  "sharpen": 0.6, "restore": "none"|"sr",
  "scenes": [{"audio": "scene0.wav", "text": "script words", "words": [{"w": "Hi", "s": 0.1, "e": 0.3}]?, "background": {...}?}]
}

stdout: one `PROGRESS {...}` json line per ~10% of every scene; last line `RESULT {...}` (or `ERROR msg` + exit 1).
Frames are streamed to ffmpeg (a 60 s 720p video needs ~100 MB, not GBs)."""
from __future__ import annotations

import json
import subprocess
import sys
import time
import traceback
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import background as bgm  # noqa: E402
import captions as cap  # noqa: E402
import face_render as fr  # noqa: E402
import facelib as fl  # noqa: E402
import formats as fm  # noqa: E402

CHUNK = 64
FPS = fr.FPS
SR = 24000


def emit(kind: str, obj):
    print(f"{kind} {json.dumps(obj) if not isinstance(obj, str) else obj}", flush=True)


def load_audio(path: str, sr: int) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32).copy()


def write_wav(path: str, x: np.ndarray, sr: int):
    import wave

    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())


class Pipeline:
    def __init__(self, spec: dict):
        self.spec = spec
        self.rdir = Path(spec["replica_dir"])
        self.fmt = spec.get("format", "16:9")
        self.size = fm.out_size(self.fmt, int(spec.get("resolution", 720)))
        self.aspect = fm.ASPECTS[self.fmt]
        self.tracker = None
        try:
            self.tracker = fl.FaceTracker()
        except Exception as e:  # noqa: BLE001
            emit("LOG", f"tracker unavailable ({e}); falling back to Haar")
        self.device = fr.pick_device()
        self.engine = None
        self.sharpen = float(spec.get("sharpen", 0.6))
        self.restore = spec.get("restore", "none")
        self.timings: dict = {}
        self.bases: dict = {}
        self.logo = fm.Logo(**spec["logo"], size=self.size) if spec.get("logo") else None
        self.cap_style = (spec.get("captions") or {}).get("style")
        self.cap_accent = (spec.get("captions") or {}).get("accent", "#ffd23f")

    def base(self, background):
        key = bgm.spec_key(background)
        if key not in self.bases:
            t = time.time()
            # the default 16:9 + no background base is the same one the live service uses (shared cache)
            asp = None if self.fmt == "16:9" else self.aspect
            b = fr.prepare_base(self.rdir, self.tracker, aspect=asp, background=background)
            H, W = b.frames[0].shape[:2]
            b.reframer = fm.Reframer((H, W), b.pts, self.size)
            self.bases[key] = b
            self.timings[f"base_{key}"] = round(time.time() - t, 2)
        return self.bases[key]

    def eng(self):
        if self.engine is None:
            t = time.time()
            self.engine = fr.Wav2LipEngine(self.device)
            self.timings["model_load"] = round(time.time() - t, 2)
        return self.engine

    def scene_frames(self, base, a16: np.ndarray, words: list | None):
        """Yield (output_frame BGR, clean_frame_or_None) for one scene, composited + reframed + captioned."""
        eng = self.eng()
        n = int(len(a16) / 16000 * FPS)
        seq = fr.pingpong_seq(base, n)
        chunks = eng.mel_chunks(a16)
        sa = fr.speech_alpha(a16, n)
        rend = None
        if self.cap_style and words:
            rend = cap.CaptionRenderer(self.cap_style, self.size, self.cap_accent)
            rend.set_words(words)
        for c0 in range(0, n, CHUNK):
            c1 = min(n, c0 + CHUNK)
            outs = eng.generate(base, seq[c0:c1], chunks[c0:c1])
            fr.sync(self.device)
            if self.restore != "none":
                from restore import restore_faces
                outs = restore_faces(outs, self.restore, self.device)
            for j in range(c1 - c0):
                i = c0 + j
                f = fr.paste(base, seq[i], outs[j], sharpen=self.sharpen, alpha=float(sa[i]))
                f = base.reframer(f, seq[i])
                clean = f.copy() if i in self._thumb_idx else None
                if rend:
                    rend.draw(f, i / FPS)
                if self.logo:
                    self.logo.draw(f)
                yield f, clean
            emit("PROGRESS", {"frac_scene": round(c1 / n, 3)})

    def run(self):
        spec = self.spec
        scenes = spec["scenes"]
        tr_kind = spec.get("transition", "fade") if len(scenes) > 1 else "cut"
        if tr_kind not in fm.TRANSITIONS:
            raise ValueError(f"unknown transition {tr_kind!r}")
        ov = int(round(float(spec.get("transition_s", 0.4)) * FPS)) if tr_kind != "cut" else 0
        pad = (ov / FPS / 2) if ov else 0.0           # silence on each side of a scene, so the transition sits on silence
        t_start = time.time()
        # ---- audio: per scene 24 kHz float, padded to whole frames; model audio is the 16 kHz resample of the SAME array
        from scipy.signal import resample_poly

        a24s, texts, words_l, bg_l = [], [], [], []
        for sc in scenes:
            a = load_audio(sc["audio"], SR)
            a = fm.pad_audio_to_frames(a, SR, FPS, lead=pad if len(scenes) > 1 else 0, tail=pad if len(scenes) > 1 else 0)
            a24s.append(a)
            texts.append(sc.get("text", ""))
            bg_l.append(sc.get("background", spec.get("background")))
            raw = sc.get("words")
            ws = None
            if raw:  # Whisper ran on the UNPADDED scene audio -> shift by the lead silence we add
                shifted = [{"w": x["w"], "s": x["s"] + (pad if len(scenes) > 1 else 0), "e": x["e"] + (pad if len(scenes) > 1 else 0)} for x in raw]
                ws = cap.align_words(sc.get("text", ""), shifted, len(a) / SR) or None
            if ws is None:
                ws = cap.uniform_words(sc.get("text", ""), resample_poly(a, 2, 3))
            words_l.append(ws)
        # thumbnail candidates: a few frames early in the first scene
        n0 = len(a24s[0]) // int(SR / FPS)
        self._thumb_idx = {int(n0 * q) for q in (0.12, 0.2, 0.28, 0.36, 0.45)} if spec.get("thumbnail") else set()
        # ---- output audio (overlap-add with crossfade over the padded silence)
        final = fm.overlap_add(a24s, int(ov / FPS * SR) if ov else 0)
        tmp_wav = str(Path(spec["out"]).with_suffix(".mix.wav"))
        write_wav(tmp_wav, final, SR)
        Path(spec["out"]).parent.mkdir(parents=True, exist_ok=True)
        W, H = self.size
        enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}", "-r", str(FPS),
                                "-i", "-", "-i", tmp_wav, "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-crf", "18", "-preset", "veryfast",
                                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", "-shortest", spec["out"]],
                               stdin=subprocess.PIPE)
        held: list[np.ndarray] = []      # the last `ov` frames of the previous scene, waiting to be blended with the next one
        thumbs: list[np.ndarray] = []
        total = 0
        try:
            for si, a24 in enumerate(a24s):
                emit("PROGRESS", {"stage": "scene", "scene": si + 1, "scenes": len(scenes), "frac_scene": 0})
                base = self.base(bg_l[si])
                a16 = resample_poly(a24, 2, 3).astype(np.float32)
                n = len(a24) // int(SR / FPS)
                t_scene = time.time()
                k = 0
                pending: list[np.ndarray] = []
                for f, clean in self.scene_frames(base, a16, words_l[si]):
                    if clean is not None and si == 0:
                        thumbs.append(clean)
                    if held and k < len(held):
                        t = (k + 1) / (len(held) + 1)
                        f = fm.blend_transition(tr_kind, held[k], f, t)
                    k += 1
                    if ov and si < len(a24s) - 1 and k > n - ov:   # tail of this scene: hold back for the transition
                        pending.append(f)
                        continue
                    enc.stdin.write(np.ascontiguousarray(f).tobytes())
                    total += 1
                held = pending
                self.timings[f"scene{si + 1}_s"] = round(time.time() - t_scene, 2)
            enc.stdin.close()
            if enc.wait() != 0:
                raise RuntimeError("ffmpeg encode failed")
        finally:
            Path(tmp_wav).unlink(missing_ok=True)
        if spec.get("thumbnail") and thumbs:
            best = self.pick_thumb(thumbs)
            cv2.imwrite(spec["thumbnail"], best, [cv2.IMWRITE_JPEG_QUALITY, 92])
        if spec.get("srt"):
            off = 0.0
            allw = []
            for si, ws in enumerate(words_l):
                allw += [cap.Word(w.text, w.s + off, w.e + off) for w in ws]
                off += len(a24s[si]) / SR - (ov / FPS if ov else 0)
            Path(spec["srt"]).write_text(cap.srt(allw))
        dur = total / FPS
        self.timings["total_s"] = round(time.time() - t_start, 2)
        return {"ok": True, "frames": total, "seconds": round(dur, 2), "size": list(self.size), "format": self.fmt,
                "scenes": len(scenes), "device": self.device, "timings": self.timings}

    def pick_thumb(self, cands: list[np.ndarray]) -> np.ndarray:
        """Eyes open, mouth not wide open, sharp."""
        best, best_s = cands[0], -1e9
        for c in cands:
            s = fm.sharpness(c) / 100.0
            if self.tracker is not None:
                try:
                    o = self.tracker(c)
                    if o.ok:
                        s -= 50 * o.blink + 20 * o.jaw_open
                except Exception:  # noqa: BLE001
                    pass
            if s > best_s:
                best, best_s = c, s
        return best


def main():
    spec = json.loads(Path(sys.argv[1]).read_text())
    try:
        res = Pipeline(spec).run()
        emit("RESULT", res)
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        emit("ERROR", f"{type(e).__name__}: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
