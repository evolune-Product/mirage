"""Objective evaluation of the cloning engine against a reference recording (we cannot listen).

    cd backend && MIRAGE_CLONE_REPO=<weights dir or hf repo> .venv/bin/python scripts_voice_clone_eval.py \
        --ref-from <video-or-audio> [--lang en,es,hi] [--runs 2] [--json out.json]

For each sentence: time to first audio (Chatterbox is not incremental, so = whole-sentence synthesis time), real-time
factor, speaker similarity to the reference (WeSpeaker cosine, the same model/threshold family used by consent
verification), and a Whisper round-trip word error rate. A Kokoro preset voice is scored the same way as the baseline
('how similar does a random preset voice look to this person': the number a clone must clearly beat)."""
import argparse
import asyncio
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from app import consent_verify, voiceprint  # noqa: E402
from app.voice_clone import refprep  # noqa: E402
from app.voice_clone.metrics import wer  # noqa: E402
from app.voice_clone.sidecar import SAMPLE_RATE, CloneSidecar  # noqa: E402

SENTENCES = {
    "en": ["Sure, I can help with that.",
           "The starter plan costs nineteen dollars a month, and you can cancel any time.",
           "Thanks for calling. I looked at your account, and your order shipped yesterday, so it should arrive on Friday."],
    "es": ["Claro, el plan básico cuesta diecinueve dólares al mes y puedes cancelar cuando quieras."],
    "hi": ["जी हाँ, स्टार्टर प्लान की कीमत उन्नीस डॉलर प्रति माह है, और आप कभी भी रद्द कर सकते हैं।"],
}


def pcm_to_wav(pcm: bytes, path: Path) -> np.ndarray:
    a = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    refprep.write_wav(path, a, SAMPLE_RATE)
    return a


def transcribe(path: Path, lang: str) -> str:
    if lang == "en":
        return consent_verify.transcribe(voiceprint.decode_audio(path))
    from faster_whisper import WhisperModel

    global _small
    try:
        _small
    except NameError:
        _small = WhisperModel("small", device="cpu", compute_type="int8")
    segs, _ = _small.transcribe(voiceprint.decode_audio(path), language=lang, beam_size=1)
    return " ".join(s.text.strip() for s in segs).strip()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref-from", required=True)
    ap.add_argument("--lang", default="en")
    ap.add_argument("--runs", type=int, default=2)
    ap.add_argument("--json")
    ap.add_argument("--kokoro-baseline", action="store_true")
    a = ap.parse_args()
    tmp = Path(tempfile.mkdtemp(prefix="cloneeval_"))
    ref = tmp / "reference.wav"
    st = refprep.prepare_reference(Path(a.ref_from), ref)
    print("reference:", st.asdict(), flush=True)
    ref_emb = voiceprint.embed(voiceprint.decode_audio(ref))

    sc = CloneSidecar()
    t = time.time(); sc.start(); start_s = time.time() - t
    t = time.time(); sc.prepare(ref); prep_s = time.time() - t
    print(f"sidecar start {start_s:.1f}s, speaker conditioning {prep_s:.2f}s", flush=True)
    rows = []
    for lang in a.lang.split(","):
        for si, text in enumerate(SENTENCES[lang]):
            for run in range(a.runs):
                t0 = time.time(); first = None; pcm = b""
                for c in sc.stream(text, ref, lang):
                    first = first or time.time() - t0
                    pcm += c
                total = time.time() - t0
                out = tmp / f"{lang}_{si}_{run}.wav"
                audio = pcm_to_wav(pcm, out)
                dur = len(audio) / SAMPLE_RATE
                try:  # clips with < 1.5 s of speech cannot be scored by the speaker model
                    sim = voiceprint.similarity(ref_emb, voiceprint.embed(voiceprint.decode_audio(out)))
                except ValueError:
                    sim = None
                heard = transcribe(out, lang)
                r = dict(lang=lang, sentence=si, run=run, first_audio_s=round(first, 2), total_s=round(total, 2),
                         audio_s=round(dur, 2), rtf=round(total / dur, 2), similarity=None if sim is None else round(sim, 3),
                         wer=round(wer(text, heard), 3), heard=heard, wav=str(out))
                rows.append(r)
                print(json.dumps(r, ensure_ascii=False), flush=True)
    if a.kokoro_baseline:
        from app.pipeline.local import KokoroTTS

        k = KokoroTTS()
        for v in ("af_heart", "am_michael", "bm_george", "af_nova"):
            pcm = b"".join(asyncio.run(_collect(k, SENTENCES["en"][2], v)))
            out = tmp / f"kokoro_{v}.wav"
            pcm_to_wav(pcm, out)
            sim = voiceprint.similarity(ref_emb, voiceprint.embed(voiceprint.decode_audio(out)))
            print(f"kokoro baseline {v}: similarity to reference {sim:.3f}")
    warm = [r for r in rows if r["run"] >= 1 or a.runs == 1]
    sims = [r["similarity"] for r in warm if r["similarity"] is not None]
    print("\nSUMMARY (warm runs)")
    for lang in a.lang.split(","):
        w = [r for r in warm if r["lang"] == lang]
        s = [r["similarity"] for r in w if r["similarity"] is not None]
        print(f"  {lang}: rtf median {statistics.median(r['rtf'] for r in w):.2f}, first-audio median "
              f"{statistics.median(r['first_audio_s'] for r in w):.2f}s, WER mean {statistics.mean(r['wer'] for r in w):.3f}, "
              f"similarity mean {statistics.mean(s) if s else float('nan'):.3f}")
    if a.json:
        Path(a.json).write_text(json.dumps({"reference": st.asdict(), "start_s": start_s, "prep_s": prep_s, "rows": rows}, indent=1,
                                           ensure_ascii=False))
    sc.close()


async def _collect(tts, text, voice):
    return [c async for c in tts.synthesize(text, voice)]


if __name__ == "__main__":
    main()
