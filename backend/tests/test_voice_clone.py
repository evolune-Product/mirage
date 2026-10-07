"""Voice cloning: consent gate, reference extraction, lifecycle, deletion, live routing + fallback, offline video voice.
All with a fake sidecar (no model); the real engine is exercised by the scripts in docs/overnight/voice-cloning.md."""
import asyncio
import wave
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app import consent_verify, db, jobs, safety, voiceprint
from app.db import Persona, Replica
from app.main import app
from app.models_billing import AuditLog, ConsentRecord
from app.models_voice import ReplicaVoice
from app.pipeline import providers_clone as pc
from app.voice_clone import refprep, service
from app.voice_clone.metrics import wer
from app.voice_clone.sidecar import CloneUnavailable, set_default_sidecar

SPEECH = np.frombuffer((Path(__file__).parent / "fixtures" / "speech_16k.pcm").read_bytes(), dtype=np.int16)


def _speech_wav(path: Path, seconds: float = 20.0, sr: int = 24000, noise: float = 0.0) -> Path:
    """Fixture speech looped to `seconds` with short pauses, resampled to `sr` (linear interp is fine for a test)."""
    x = SPEECH.astype(np.float32) / 32768.0
    x24 = np.interp(np.linspace(0, len(x) - 1, int(len(x) * sr / 16000)), np.arange(len(x)), x).astype(np.float32)
    gap = np.zeros(int(0.6 * sr), dtype=np.float32)
    parts, n = [], 0
    while n < seconds * sr:
        parts += [x24, gap]
        n += len(x24) + len(gap)
    y = np.concatenate(parts)[: int(seconds * sr)]
    if noise:
        y = y + np.random.default_rng(0).normal(0, noise, len(y)).astype(np.float32)
    refprep.write_wav(path, y, sr)
    return path


class FakeSidecar:
    """Stands in for the Chatterbox process: 'speaks' with the fixture speech so similarity/WER code has real audio."""

    def __init__(self, fail: bool = False, partial: bool = False):
        self.fail, self.partial, self.calls, self.prepared, self.cancelled = fail, partial, [], [], 0
        x = np.interp(np.linspace(0, len(SPEECH) - 1, int(len(SPEECH) * 1.5)), np.arange(len(SPEECH)), SPEECH.astype(np.float32))
        self.pcm = x.astype(np.int16).tobytes()

    def stream(self, text, ref, lang="en"):
        self.calls.append((text, str(ref), lang))
        if self.fail:
            raise CloneUnavailable("boom")
        half = len(self.pcm) // 2
        yield self.pcm[:half]
        if self.partial:
            raise CloneUnavailable("died mid-sentence")
        yield self.pcm[half:]

    def synth(self, text, ref, lang="en"):
        return b"".join(self.stream(text, ref, lang))

    def prepare(self, ref):
        self.prepared.append(str(ref))

    async def astream(self, text, ref, lang="en"):
        try:
            for c in self.stream(text, ref, lang):
                yield c
        except GeneratorExit:
            self.cancelled += 1
            raise


class FakeBase:
    sample_rate = 24000

    def __init__(self):
        self.voices = []

    async def synthesize(self, text, voice="default"):
        self.voices.append(voice)
        yield b"\x01\x00" * 2400

    def warmup(self):
        pass


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "engine", create_engine("sqlite://", connect_args={"check_same_thread": False},
                                                    poolclass=StaticPool))
    SQLModel.metadata.create_all(db.engine)
    monkeypatch.setattr(jobs, "DATA_DIR", tmp_path)
    monkeypatch.setenv("VOCALFACE_SECRET_KEY", "test-secret-key-0123456789")
    monkeypatch.setenv("VOCALFACE_RL_SIGNUP", "1000/60")
    monkeypatch.setattr(consent_verify, "transcribe", lambda wav: service.TEST_SENTENCE)  # no Whisper in unit tests
    service._cache.clear()
    safety.limiter.reset()
    fake = FakeSidecar()
    set_default_sidecar(fake)

    def sess():
        with Session(db.engine) as s:
            yield s

    app.dependency_overrides[db.get_session] = sess
    c = TestClient(app)
    c.fake = fake
    yield c
    app.dependency_overrides.clear()
    set_default_sidecar(None)
    service._cache.clear()
    safety.limiter.reset()


def signup(c, email="a@b.co"):
    r = c.post("/v1/signup", json={"email": email})
    return r.json()["account_id"], {"x-api-key": r.json()["api_key"]}


def make_replica(c, tmp_path, acc_id, ready=True, audio=True, consent="asr-phrase+voice-match"):
    with Session(db.engine) as s:
        r = Replica(account_id=acc_id, name="Bob", train_video_url="x", status="ready" if ready else "training")
        s.add(r); s.commit(); s.refresh(r)
        rid = r.id
        if consent:
            s.add(ConsentRecord(replica_id=rid, account_id=acc_id, speaker_name="Bob", phrase="p", transcript="t",
                                audio_url="u", verified_by=consent))
            s.commit()
    d = jobs.replica_dir(rid)
    d.mkdir(parents=True, exist_ok=True)
    if audio:
        _speech_wav(d / "voice_ref.wav", 22.0)
    return rid


def actions(rid):
    with Session(db.engine) as s:
        return [a.action for a in s.exec(select(AuditLog).where(AuditLog.target == rid)).all()]


# ------------------------------------------------------------------ pure helpers
def test_wer():
    assert wer("Hello there, friend.", "hello there friend") == 0
    assert wer("a b c d", "a x c") == pytest.approx(0.5)
    assert wer("", "") == 0 and wer("", "x") == 1


def test_parse_voice():
    assert service.parse_voice("clone:r_1") == ("r_1", "en")
    assert service.parse_voice("clone:r_1@es") == ("r_1", "es")
    assert service.parse_voice("clone", "r_9") == ("r_9", "en")
    assert service.parse_voice("af_heart") is None and service.parse_voice("cloned_x") is None


def test_refprep_picks_clean_window_and_rejects_noise(tmp_path):
    src = _speech_wav(tmp_path / "long.wav", 60.0, noise=0.002)
    st = refprep.prepare_reference(src, tmp_path / "ref.wav")
    assert 8 <= st.seconds <= 20.5 and st.clipped_fraction == 0 and st.snr_db > 10
    x, sr = refprep.read_wav(tmp_path / "ref.wav")
    assert sr == 24000 and len(x) / sr == pytest.approx(st.seconds, abs=0.1) and 0.3 < np.abs(x).max() < 0.99
    noise = np.random.default_rng(1).normal(0, 0.05, 24000 * 12).astype(np.float32)
    refprep.write_wav(tmp_path / "noise.wav", noise)
    with pytest.raises(refprep.ReferenceError):
        refprep.prepare_reference(tmp_path / "noise.wav", tmp_path / "o.wav")
    refprep.write_wav(tmp_path / "sil.wav", np.zeros(24000 * 5, np.float32))
    with pytest.raises(refprep.ReferenceError):
        refprep.prepare_reference(tmp_path / "sil.wav", tmp_path / "o2.wav")


# ------------------------------------------------------------------ consent gate + lifecycle API
def test_refused_without_consent_or_unverified_voice(env, tmp_path):
    acc, h = signup(env)
    rid = make_replica(env, tmp_path, acc, consent=None)
    r = env.post(f"/v1/replicas/{rid}/voice", headers=h)
    assert r.status_code == 403 and "consent" in r.json()["detail"]["message"]
    assert not (service.voice_dir(rid)).exists()
    assert "voice.clone_refused" in actions(rid)
    rid2 = make_replica(env, tmp_path, acc, consent="asr-phrase (voice:mismatch)")  # spoken, but not the same voice
    r = env.post(f"/v1/replicas/{rid2}/voice", headers=h)
    assert r.status_code == 403 and "not matched" in r.json()["detail"]["message"]
    rid3 = make_replica(env, tmp_path, acc, ready=False)
    assert env.post(f"/v1/replicas/{rid3}/voice", headers=h).status_code == 409
    rid4 = make_replica(env, tmp_path, acc, audio=False)
    assert env.post(f"/v1/replicas/{rid4}/voice", headers=h).status_code == 409
    assert env.fake.calls == []


def test_revoked_consent_refuses(env, tmp_path):
    acc, h = signup(env)
    rid = make_replica(env, tmp_path, acc)
    with Session(db.engine) as s:
        for c in s.exec(select(ConsentRecord)).all():
            c.revoked = True; s.add(c)
        s.commit()
    assert env.post(f"/v1/replicas/{rid}/voice", headers=h).status_code == 403


def test_create_get_delete_and_listing(env, tmp_path):
    acc, h = signup(env)
    rid = make_replica(env, tmp_path, acc)
    assert env.get(f"/v1/replicas/{rid}/voice", headers=h).json()["status"] == "none"
    r = env.post(f"/v1/replicas/{rid}/voice", headers=h)
    assert r.status_code == 202
    v = env.get(f"/v1/replicas/{rid}/voice", headers=h).json()  # TestClient ran the background task
    assert v["status"] == "ready" and v["cloned"] is True and v["voice_id"] == f"clone:{rid}"
    assert v["usable_in_conversations"] and 8 <= v["reference_seconds"] <= 20.5
    assert v["wer"] == 0 and v["synth_rtf"] is not None and "notice" in v
    assert v["similarity"] is None or -1 <= v["similarity"] <= 1
    assert service.ref_path(rid).exists()
    assert env.fake.calls and env.fake.calls[0][2] == "en"
    voices = env.get("/v1/voices", headers=h).json()["voices"]
    cl = [x for x in voices if x.get("cloned")]
    assert len(cl) == 1 and cl[0]["id"] == f"clone:{rid}" and cl[0]["replica_id"] == rid
    # other accounts: 404 on the replica, and the voice is not in their catalogue
    _, h2 = signup(env, "x@y.co")
    assert env.get(f"/v1/replicas/{rid}/voice", headers=h2).status_code == 404
    assert env.delete(f"/v1/replicas/{rid}/voice", headers=h2).status_code == 404
    assert not [x for x in env.get("/v1/voices", headers=h2).json()["voices"] if x.get("cloned")]
    # idempotent POST
    n = len(env.fake.calls)
    assert env.post(f"/v1/replicas/{rid}/voice", headers=h).json()["status"] == "ready" and len(env.fake.calls) == n
    # delete
    assert env.delete(f"/v1/replicas/{rid}/voice", headers=h).json() == {"deleted": True}
    assert not service.voice_dir(rid).exists()
    assert env.get(f"/v1/replicas/{rid}/voice", headers=h).json()["status"] == "none"
    a = actions(rid)
    for want in ("voice.clone_requested", "voice.clone_ready", "voice.clone_deleted"):
        assert want in a


def test_consent_revocation_deletes_voice_and_blocks_use(env, tmp_path):
    acc, h = signup(env)
    rid = make_replica(env, tmp_path, acc)
    env.post(f"/v1/replicas/{rid}/voice", headers=h)
    assert service.usable_reference(rid, acc) is not None
    r = env.delete(f"/v1/replicas/{rid}/consent", headers=h)
    assert r.status_code == 200
    assert not service.voice_dir(rid).exists()  # stored voice data gone
    v = env.get(f"/v1/replicas/{rid}/voice", headers=h).json()
    assert v["status"] == "revoked" and not v["usable_in_conversations"] and v["reference_quality"] == {}
    service._cache.clear()
    assert service.usable_reference(rid, acc) is None
    assert env.post(f"/v1/replicas/{rid}/voice", headers=h).status_code == 403
    assert "voice.clone_revoked" in actions(rid)


def test_gate_purges_even_if_consent_revoked_behind_our_back(env, tmp_path):
    """Defence in depth: revoke by writing the DB directly (no endpoint hook) - the synthesis-time gate still refuses."""
    acc, h = signup(env)
    rid = make_replica(env, tmp_path, acc)
    env.post(f"/v1/replicas/{rid}/voice", headers=h)
    with Session(db.engine) as s:
        for c in s.exec(select(ConsentRecord)).all():
            c.revoked = True; s.add(c)
        s.commit()
    service._cache.clear()
    assert service.usable_reference(rid) is None
    assert not service.voice_dir(rid).exists()


def test_replica_and_account_deletion_remove_voice(env, tmp_path):
    acc, h = signup(env)
    rid = make_replica(env, tmp_path, acc)
    env.post(f"/v1/replicas/{rid}/voice", headers=h)
    assert service.voice_dir(rid).exists()
    assert env.delete(f"/v1/replicas/{rid}", headers=h).status_code == 200
    assert not service.voice_dir(rid).exists()
    with Session(db.engine) as s:
        assert s.get(ReplicaVoice, rid) is None
    rid2 = make_replica(env, tmp_path, acc)
    env.post(f"/v1/replicas/{rid2}/voice", headers=h)
    assert env.post("/v1/account/delete-my-data", json={"confirm": "delete-my-data"}, headers=h).status_code == 200
    assert not service.voice_dir(rid2).exists()
    with Session(db.engine) as s:
        assert s.get(ReplicaVoice, rid2) is None


def test_failed_engine_marks_failed_and_leaves_no_audio(env, tmp_path):
    acc, h = signup(env)
    set_default_sidecar(FakeSidecar(fail=True))
    rid = make_replica(env, tmp_path, acc)
    env.post(f"/v1/replicas/{rid}/voice", headers=h)
    v = env.get(f"/v1/replicas/{rid}/voice", headers=h).json()
    assert v["status"] == "failed" and "boom" in v["error"] and not v["usable_in_conversations"]
    assert not service.voice_dir(rid).exists()
    assert "voice.clone_failed" in actions(rid)


def test_low_similarity_clone_is_rejected(env, tmp_path, monkeypatch):
    acc, h = signup(env)
    monkeypatch.setattr(voiceprint, "compare_files", lambda a, b: 0.05)
    rid = make_replica(env, tmp_path, acc)
    env.post(f"/v1/replicas/{rid}/voice", headers=h)
    v = env.get(f"/v1/replicas/{rid}/voice", headers=h).json()
    assert v["status"] == "failed" and "resemble" in v["error"]


def test_preview_is_gated_moderated_and_labelled(env, tmp_path):
    acc, h = signup(env)
    rid = make_replica(env, tmp_path, acc)
    assert env.post(f"/v1/replicas/{rid}/voice/preview", json={"text": "hi"}, headers=h).status_code == 409  # not cloned yet
    env.post(f"/v1/replicas/{rid}/voice", headers=h)
    r = env.post(f"/v1/replicas/{rid}/voice/preview", json={"text": "Hello world."}, headers=h)
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav" and r.content[:4] == b"RIFF"
    assert r.headers["x-vocalface-synthetic-voice"] == "cloned"
    assert env.post(f"/v1/replicas/{rid}/voice/preview", json={"text": "x" * 301}, headers=h).status_code == 422
    assert "voice.clone_used" in actions(rid)


# ------------------------------------------------------------------ persona selection
def test_persona_voice_validation_and_runtime_resolution(env, tmp_path):
    acc, h = signup(env)
    _, h2 = signup(env, "x@y.co")
    rid = make_replica(env, tmp_path, acc)
    body = {"name": "P", "system_prompt": "s", "tts_voice": f"clone:{rid}"}
    assert env.post("/v1/personas", json=body, headers=h).status_code == 404  # no cloned voice yet
    env.post(f"/v1/replicas/{rid}/voice", headers=h)
    r = env.post("/v1/personas", json=body, headers=h)
    assert r.status_code == 200 and r.json()["tts_voice"] == f"clone:{rid}"
    assert env.post("/v1/personas", json=body, headers=h2).status_code == 404  # someone else's voice
    assert env.post("/v1/personas", json={**body, "tts_voice": "clone:r_nope"}, headers=h).status_code == 404
    p = Persona(account_id=acc, name="P", system_prompt="s", tts_voice=f"clone:{rid}")
    assert pc.resolve_persona_voice(p) == f"clone:{rid}"
    p2 = Persona(account_id="acc_other", name="P", system_prompt="s", tts_voice=f"clone:{rid}")
    assert pc.resolve_persona_voice(p2) == "default"  # direct DB write cannot borrow another account's voice
    assert pc.resolve_persona_voice(Persona(account_id=acc, name="n", system_prompt="s", tts_voice="af_nova")) == "af_nova"


# ------------------------------------------------------------------ live TTS routing + fallback
def _ready_voice(env, tmp_path):
    acc, h = signup(env)
    rid = make_replica(env, tmp_path, acc)
    env.post(f"/v1/replicas/{rid}/voice", headers=h)
    return acc, rid


async def _collect(tts, text, voice):
    return [c async for c in tts.synthesize(text, voice)]


def test_live_routing_uses_clone_and_passes_other_voices_through(env, tmp_path):
    acc, rid = _ready_voice(env, tmp_path)
    base, sc = FakeBase(), FakeSidecar()
    tts = pc.CloneRoutingTTS(base, sc)
    out = asyncio.run(_collect(tts, "Hello there.", f"clone:{rid}"))
    assert len(out) == 2 and sc.calls[0][2] == "en" and base.voices == []
    asyncio.run(_collect(tts, "Hi.", "af_heart"))
    assert base.voices == ["af_heart"] and len(sc.calls) == 1
    assert tts.warmup() is None and tts.sample_rate == 24000  # proxied attribute access
    asyncio.run(tts.warm_voice(f"clone:{rid}"))
    assert sc.prepared == [str(service.ref_path(rid))]
    asyncio.run(_collect(tts, "Hola.", f"clone:{rid}@es"))
    assert sc.calls[-1][2] == "es"


def test_live_fallback_to_kokoro_on_failure_without_double_speaking(env, tmp_path):
    acc, rid = _ready_voice(env, tmp_path)
    base = FakeBase()
    tts = pc.CloneRoutingTTS(base, FakeSidecar(fail=True))
    out = asyncio.run(_collect(tts, "Hello there.", f"clone:{rid}"))
    assert out == [b"\x01\x00" * 2400] and base.voices == ["af_heart"]
    assert "voice.clone_fallback" in actions(rid)
    base2 = FakeBase()  # partial audio then failure: the rest is dropped, no Kokoro repeat
    tts2 = pc.CloneRoutingTTS(base2, FakeSidecar(partial=True))
    out2 = asyncio.run(_collect(tts2, "Hello there.", f"clone:{rid}"))
    assert len(out2) == 1 and base2.voices == []


def test_live_breaker_stops_hammering_a_dead_engine(env, tmp_path, monkeypatch):
    acc, rid = _ready_voice(env, tmp_path)
    monkeypatch.setattr(pc, "BREAKER_FAILS", 2)
    sc, base = FakeSidecar(fail=True), FakeBase()
    tts = pc.CloneRoutingTTS(base, sc)
    for _ in range(4):
        asyncio.run(_collect(tts, "Hi.", f"clone:{rid}"))
    assert len(sc.calls) == 2 and len(base.voices) == 4  # breaker open after 2 failures


def test_live_revoked_consent_midcall_falls_back_and_deletes(env, tmp_path):
    acc, rid = _ready_voice(env, tmp_path)
    base, sc = FakeBase(), FakeSidecar()
    tts = pc.CloneRoutingTTS(base, sc)
    asyncio.run(_collect(tts, "One.", f"clone:{rid}"))
    with Session(db.engine) as s:
        for c in s.exec(select(ConsentRecord)).all():
            c.revoked = True; s.add(c)
        s.commit()
    service._cache.clear()
    asyncio.run(_collect(tts, "Two.", f"clone:{rid}"))
    assert len(sc.calls) == 1 and base.voices == ["af_heart"] and not service.voice_dir(rid).exists()


def test_live_unknown_replica_voice_never_reaches_engine(env, tmp_path):
    base, sc = FakeBase(), FakeSidecar()
    tts = pc.CloneRoutingTTS(base, sc)
    asyncio.run(_collect(tts, "Hi.", "clone:r_doesnotexist"))
    assert sc.calls == [] and base.voices == ["af_heart"]


# ------------------------------------------------------------------ offline video voice
class FakeKokoro:
    def __init__(self):
        self.calls = []

    def synthesize(self, text, out_wav, voice_ref, voice="default"):
        self.calls.append(voice)
        with wave.open(str(out_wav), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000); w.writeframes(b"\0\0" * 2400)


def test_video_voice_clone_default_and_fallback(env, tmp_path):
    acc, rid = _ready_voice(env, tmp_path)
    ref = jobs.replica_dir(rid) / "voice_ref.wav"
    sc, kk = FakeSidecar(), FakeKokoro()
    v = pc.CloneAwareVoice(kk, sc)
    out = tmp_path / "a.wav"
    v.synthesize("First sentence. Second sentence here! Third?", out, ref, "clone")
    assert v.last_engine == "clone" and kk.calls == [] and len(sc.calls) == 3
    with wave.open(str(out)) as w:
        assert w.getframerate() == 24000 and w.getnframes() > 24000 * 3
    v.synthesize("Hola.", tmp_path / "b.wav", ref, f"clone:{rid}@es")
    assert sc.calls[-1][2] == "es"
    v.synthesize("Plain.", tmp_path / "c.wav", ref, "default")
    assert v.last_engine == "kokoro" and kk.calls == ["default"]
    # a video cannot use some other replica's cloned voice
    v.synthesize("Nope.", tmp_path / "d.wav", ref, "clone:r_someone_else")
    assert v.last_engine == "kokoro" and (tmp_path / "d.wav").exists()
    # engine failure -> Kokoro, video still gets audio
    v2 = pc.CloneAwareVoice(kk, FakeSidecar(fail=True))
    v2.synthesize("Hi there.", tmp_path / "e.wav", ref, "clone")
    assert v2.last_engine == "kokoro" and (tmp_path / "e.wav").exists()
    assert "voice.clone_fallback" in actions(rid) and "voice.clone_used" in actions(rid)


def test_video_voice_refused_after_revocation(env, tmp_path):
    acc, h = signup(env)
    rid = make_replica(env, tmp_path, acc)
    env.post(f"/v1/replicas/{rid}/voice", headers=h)
    env.delete(f"/v1/replicas/{rid}/consent", headers=h)
    sc, kk = FakeSidecar(), FakeKokoro()
    pc.CloneAwareVoice(kk, sc).synthesize("Hi.", tmp_path / "x.wav", jobs.replica_dir(rid) / "voice_ref.wav", "clone")
    assert sc.calls == [] and kk.calls  # Kokoro spoke


def test_worker_hook_auto_creates_voice_only_with_consent(env, tmp_path, monkeypatch):
    acc, h = signup(env)
    with_c = make_replica(env, tmp_path, acc)
    without = make_replica(env, tmp_path, acc, consent=None)
    service.auto_after_replica_ready(with_c, env.fake)
    service.auto_after_replica_ready(without, env.fake)
    with Session(db.engine) as s:
        assert s.get(ReplicaVoice, with_c).status == "ready"
        assert s.get(ReplicaVoice, without) is None
    monkeypatch.setenv("VOCALFACE_VOICE_CLONE_AUTO", "0")
    other = make_replica(env, tmp_path, acc)
    service.auto_after_replica_ready(other, env.fake)
    with Session(db.engine) as s:
        assert s.get(ReplicaVoice, other) is None


def test_production_rejects_typed_consent_for_cloning(env, tmp_path, monkeypatch):
    acc, h = signup(env)
    rid = make_replica(env, tmp_path, acc, consent="typed-transcript (dev only)")
    assert env.post(f"/v1/replicas/{rid}/voice", headers=h).status_code == 202  # dev: allowed
    monkeypatch.setenv("VOCALFACE_ENV", "production")
    rid2 = make_replica(env, tmp_path, acc, consent="typed-transcript (dev only)")
    with Session(db.engine) as s:
        assert service.active_consent(s, rid2) is None


# ------------------------------------------------------------------ audio_checks (artifact detection)
def test_audio_checks_flags_clipping_silence_clicks_and_passes_speech():
    from app.voice_clone import audio_checks as ac

    x = SPEECH.astype(np.float32) / 32768.0
    x = np.interp(np.linspace(0, len(x) - 1, int(len(x) * 1.5)), np.arange(len(x)), x).astype(np.float32)
    good = ac.check(x * (0.6 / np.abs(x).max()), 24000)
    assert good["ok"], good["problems"]
    assert any("clipping" in p for p in ac.check(np.clip(x * 20, -1, 1), 24000)["problems"])
    assert any("silence" in p for p in ac.check(np.zeros(48000, np.float32), 24000)["problems"])
    c = (x * 0.3).copy(); c[len(c) // 2:len(c) // 2 + 2] = [0.9, -0.9]
    assert ac.check(c, 24000)["click_count"] >= 1
    assert any("NaN" in p for p in ac.check(np.r_[x, np.nan].astype(np.float32), 24000)["problems"])
    assert any("shorter" in p for p in ac.check(x[:100], 24000)["problems"])
    noise = np.random.default_rng(0).normal(0, 0.1, 48000).astype(np.float32)
    assert any("noise-like" in p for p in ac.check(noise, 24000)["problems"])
