import io
import shutil
from pathlib import Path

import pytest

from app import jobs, signing, storage
from app.storage import LocalStorage, S3Storage


class ClientError(Exception):
    def __init__(self, code):
        self.response = {"Error": {"Code": code}}
        super().__init__(f"An error occurred ({code})")


class FakeS3:
    """Just enough of the boto3 S3 client surface that S3Storage uses."""
    def __init__(self):
        self.objs: dict[tuple[str, str], tuple[bytes, dict]] = {}
        self.presigned: list[dict] = []

    def upload_file(self, path, bucket, key, ExtraArgs=None):
        self.objs[(bucket, key)] = (Path(path).read_bytes(), ExtraArgs or {})

    def download_file(self, bucket, key, path):
        if (bucket, key) not in self.objs:
            raise ClientError("404")
        Path(path).write_bytes(self.objs[(bucket, key)][0])

    def head_object(self, Bucket, Key):
        if (Bucket, Key) not in self.objs:
            raise ClientError("404")
        return {}

    def delete_object(self, Bucket, Key):
        self.objs.pop((Bucket, Key), None)

    def delete_objects(self, Bucket, Delete):
        for o in Delete["Objects"]:
            self.objs.pop((Bucket, o["Key"]), None)

    def list_objects_v2(self, Bucket, Prefix="", ContinuationToken=None):
        keys = sorted(k for (b, k) in self.objs if b == Bucket and k.startswith(Prefix))
        return {"Contents": [{"Key": k} for k in keys], "IsTruncated": False}

    def generate_presigned_url(self, op, Params, ExpiresIn):
        self.presigned.append({"op": op, **Params, "exp": ExpiresIn})
        return f"https://bucket.example/{Params['Key']}?X-Amz-Expires={ExpiresIn}&X-Amz-Signature=abc"


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()
    storage.set_storage(None)
    yield tmp_path / "data"
    storage.set_storage(None)


def test_local_is_default_and_paths_unchanged(data, monkeypatch):
    monkeypatch.delenv("MIRAGE_STORAGE", raising=False)
    st = storage.get_storage()
    assert isinstance(st, LocalStorage) and st.root == data
    p = jobs.video_path("v_abc123")
    p.parent.mkdir(parents=True); p.write_bytes(b"mp4data")
    assert storage.key_for(p) == "videos/v_abc123.mp4"
    storage.publish(p)  # no-op
    assert storage.exists(p) and storage.ensure_local(p)
    st.put_bytes("replicas/r_1/face.png", b"png")
    assert (data / "replicas/r_1/face.png").read_bytes() == b"png"
    assert st.list("replicas") == ["replicas/r_1/face.png"]
    assert st.delete_prefix("replicas/r_1") == 1 and not (data / "replicas/r_1").exists()


def test_keys_cannot_escape(data):
    for bad in ("../x", "/etc/passwd", "a/../../b", ""):
        with pytest.raises(ValueError):
            LocalStorage(data).exists(bad)
    with pytest.raises(ValueError):
        storage.key_for("/tmp/elsewhere.txt")


def test_s3_roundtrip_with_fake_client(data):
    fake = FakeS3()
    st = S3Storage("bkt", prefix="mirage/prod", client=fake)
    storage.set_storage(st)
    face = jobs.replica_dir("r_abc") / "face.png"
    face.parent.mkdir(parents=True); face.write_bytes(b"PNGDATA")
    (face.parent / "meta.json").write_text("{}")
    storage.publish(face.parent)  # directory publish
    assert ("bkt", "mirage/prod/replicas/r_abc/face.png") in fake.objs
    assert fake.objs[("bkt", "mirage/prod/replicas/r_abc/face.png")][1]["ContentType"] == "image/png"
    assert st.list("replicas/r_abc") == ["replicas/r_abc/face.png", "replicas/r_abc/meta.json"]
    # another machine: local copy gone, pull it back
    shutil.rmtree(face.parent)
    assert not face.exists() and storage.exists(face)
    assert storage.ensure_local(face) and face.read_bytes() == b"PNGDATA"
    assert not storage.ensure_local(jobs.video_path("v_missing"))
    # delete
    assert storage.remove(jobs.replica_dir("r_abc")) == 2
    assert st.list() == []


def test_s3_presign_ttl_capped_and_content_type(data):
    fake = FakeS3()
    st = S3Storage("bkt", client=fake)
    url = st.presign("videos/v_1.mp4", 300)
    assert "X-Amz-Expires=300" in url
    assert fake.presigned[0]["ResponseContentType"] == "video/mp4" and fake.presigned[0]["Key"] == "videos/v_1.mp4"


def _client(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from sqlmodel import SQLModel, create_engine

    from app import db
    from app.main import app

    eng = create_engine(f"sqlite:///{tmp_path}/api.db", connect_args={"check_same_thread": False})
    monkeypatch.setattr(db, "engine", eng)
    SQLModel.metadata.create_all(eng)
    return TestClient(app)


def test_file_endpoint_redirects_to_presigned_url_after_signature_check(data, monkeypatch, tmp_path):
    monkeypatch.setenv("MIRAGE_SECRET_KEY", "x" * 32)
    monkeypatch.setenv("MIRAGE_ALLOW_PUBLIC_FILES", "0")
    fake = FakeS3()
    storage.set_storage(S3Storage("bkt", client=fake))
    vid = "v_0123456789ab"
    fake.objs[("bkt", f"videos/{vid}.mp4")] = (b"x", {})  # only in the bucket, not on local disk
    c = _client(monkeypatch, tmp_path)
    path = f"/v1/files/videos/{vid}.mp4"
    assert c.get(path, follow_redirects=False).status_code == 403  # unsigned: refused before any presign
    assert fake.presigned == []
    r = c.get(signing.sign_path(path), follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"].startswith("https://bucket.example/videos/")
    assert "X-Amz-Expires=300" in r.headers["location"]  # capped well below the 1h signed-url TTL
    # streaming mode (no redirect) pulls it through a local cache
    monkeypatch.setenv("MIRAGE_S3_REDIRECT", "0")
    r2 = c.get(signing.sign_path(path))
    assert r2.status_code == 200 and r2.content == b"x"
    # unknown key
    assert c.get(signing.sign_path("/v1/files/videos/v_ffffffffffff.mp4")).status_code == 404


def test_local_file_endpoint_still_works(data, monkeypatch, tmp_path):
    monkeypatch.setenv("MIRAGE_SECRET_KEY", "x" * 32)
    vid = "v_aaaaaaaaaaaa"
    p = jobs.video_path(vid); p.parent.mkdir(parents=True); p.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    c = _client(monkeypatch, tmp_path)
    r = c.get(signing.sign_path(f"/v1/files/videos/{vid}.mp4"))
    assert r.status_code == 200 and r.headers["content-type"] == "video/mp4"


def test_s3_requires_bucket_and_boto3_message(monkeypatch):
    with pytest.raises(RuntimeError, match="BUCKET"):
        S3Storage("")
    monkeypatch.setenv("MIRAGE_STORAGE", "bogus")
    with pytest.raises(RuntimeError, match="unknown"):
        storage.get_storage()


def test_s3_with_moto_if_available(data):
    moto = pytest.importorskip("moto")
    boto3 = pytest.importorskip("boto3")
    with moto.mock_aws():
        c = boto3.client("s3", region_name="us-east-1")
        c.create_bucket(Bucket="mirage-test")
        st = S3Storage("mirage-test", prefix="p", client=c)
        st.put_bytes("replicas/r_1/face.png", b"abc")
        assert st.exists("replicas/r_1/face.png") and not st.exists("replicas/r_2/face.png")
        assert st.get_bytes("replicas/r_1/face.png") == b"abc"
        assert st.get_bytes("nope/x.png") is None
        url = st.presign("replicas/r_1/face.png", 60)
        assert "mirage-test" in url and "Signature" in url
        assert st.list("replicas") == ["replicas/r_1/face.png"]
        assert st.delete_prefix("replicas/r_1") == 1 and st.list() == []
