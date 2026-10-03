"""Audit WAVs produced by the voice-clone API: speaker similarity (WeSpeaker cosine) to the reference recording vs
a stock Kokoro voice, Whisper WER against the intended text, and programmatic artifact checks.

    cd backend && .venv/bin/python scripts_voice_clone_audit.py --ref ref.wav --json out.json \
        name=path.wav::"intended text"::seconds_to_synthesize ...
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from app import consent_verify, voiceprint  # noqa: E402
from app.voice_clone import audio_checks, refprep  # noqa: E402
from app.voice_clone.metrics import wer  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", required=True)
    ap.add_argument("--same-speaker", help="other recording of the same person (ceiling)")
    ap.add_argument("--json")
    ap.add_argument("items", nargs="+")
    a = ap.parse_args()
    ref_emb = voiceprint.embed(voiceprint.decode_audio(Path(a.ref)))
    rows = []
    from app.pipeline.local import KokoroTTS

    k = KokoroTTS()
    for it in a.items:
        spec, text, took = (it.split("::") + ["", ""])[:3]
        name, _, path = spec.partition("=")
        path = Path(path or name)
        x, sr = refprep.read_wav(path)
        chk = audio_checks.check(x, sr)
        emb = voiceprint.embed(voiceprint.decode_audio(path))
        row = dict(name=name, seconds=chk["seconds"], similarity=round(voiceprint.similarity(ref_emb, emb), 3), checks=chk)
        if took:
            row["synth_s"] = float(took); row["rtf"] = round(float(took) / chk["seconds"], 2)
        if text:
            heard = consent_verify.transcribe(voiceprint.decode_audio(path))
            row.update(wer=round(wer(text, heard), 3), heard=heard)
            pcm = b"".join(asyncio.run(_collect(k, text, "af_heart")))
            kp = path.with_suffix(".kokoro.wav")
            import numpy as np
            refprep.write_wav(kp, np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768, 24000)
            row["kokoro_af_heart_similarity"] = round(voiceprint.similarity(ref_emb, voiceprint.embed(voiceprint.decode_audio(kp))), 3)
            row["kokoro_checks_ok"] = audio_checks.check(*refprep.read_wav(kp))["ok"]
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    if a.json:
        Path(a.json).write_text(json.dumps(rows, indent=1, ensure_ascii=False))


async def _collect(tts, text, voice):
    return [c async for c in tts.synthesize(text, voice)]


if __name__ == "__main__":
    main()
