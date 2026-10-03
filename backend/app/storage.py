"""Object storage abstraction for replica faces, rendered videos, consent recordings and listening clips.

Model: the API/worker/lip-sync processes keep using a *local working directory* (MIRAGE_DATA) exactly as before; the
Storage layer is the durable home of those files. Keys are the POSIX path relative to MIRAGE_DATA, so
`replicas/r_ab12/face.png`, `videos/v_cd34.mp4`, `consent/r_ab12/cns_x.webm`, `replicas/r_ab12/listening.mp4`.

  local (default)  MIRAGE_STORAGE=local  - the data dir IS the store. publish() is a no-op: current behaviour and paths unchanged.
  s3               MIRAGE_STORAGE=s3     - S3-compatible (AWS, R2, MinIO, B2). boto3 is optional (pip install boto3).
                   MIRAGE_S3_BUCKET (required), MIRAGE_S3_PREFIX, MIRAGE_S3_ENDPOINT, MIRAGE_S3_REGION,
                   standard AWS credentials via the usual env vars / instance role.
                   MIRAGE_S3_REDIRECT=1 (default): file endpoints verify the Mirage signed URL first and then 307-redirect to
                   a short-lived presigned bucket URL; 0 streams the bytes through the API instead.

Typical flow with s3: a worker (any machine) writes locally, then `publish(path)` uploads it; the API serves it with
`serve(path)` which presigns; a process that needs the bytes locally (lip-sync, renderer) calls `ensure_local(path)`.
Every function here is safe to call with the local backend (cheap no-ops), so call sites need no `if`.
"""
from __future__ import annotations

import logging
import mimetypes
import os
import shutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

log = logging.getLogger("mirage.storage")

_MEDIA = {".mp4": "video/mp4", ".png": "image/png", ".webm": "audio/webm", ".wav": "audio/wav", ".ogg": "audio/ogg",
          ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".json": "application/json"}


def _ctype(key: str, given: str | None = None) -> str:
    return given or _MEDIA.get(Path(key).suffix.lower()) or mimetypes.guess_type(key)[0] or "application/octet-stream"


def data_root() -> Path:
    from . import jobs

    return Path(jobs.DATA_DIR)


def key_for(path: str | Path) -> str:
    """Local path under the data dir -> storage key. Raises ValueError for paths outside the data dir."""
    p = Path(path).resolve()
    root = data_root().resolve()
    try:
        return p.relative_to(root).as_posix()
    except ValueError:
        raise ValueError(f"{path} is not inside the data directory {root}") from None


def _check_key(key: str) -> str:
    if not key or key.startswith("/") or ".." in key.split("/"):
        raise ValueError(f"bad storage key {key!r}")
    return key


class Storage(ABC):
    name = "abstract"
    remote = False  # True when the bytes do not live in the local data dir

    @abstractmethod
    def put_file(self, key: str, local: Path, content_type: Optional[str] = None) -> None: ...
    @abstractmethod
    def get_to_file(self, key: str, local: Path) -> bool: ...  # False if the key does not exist
    @abstractmethod
    def exists(self, key: str) -> bool: ...
    @abstractmethod
    def delete(self, key: str) -> bool: ...
    @abstractmethod
    def delete_prefix(self, prefix: str) -> int: ...
    @abstractmethod
    def list(self, prefix: str = "") -> list[str]: ...

    def presign(self, key: str, ttl: int = 300, content_type: Optional[str] = None) -> Optional[str]:
        """Time-limited direct URL, or None when the backend cannot hand one out (local)."""
        return None

    def put_bytes(self, key: str, data: bytes, content_type: Optional[str] = None) -> None:
        import tempfile

        with tempfile.NamedTemporaryFile(delete=False) as t:
            t.write(data)
        try:
            self.put_file(key, Path(t.name), content_type)
        finally:
            os.unlink(t.name)

    def get_bytes(self, key: str) -> Optional[bytes]:
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x"
            return p.read_bytes() if self.get_to_file(key, p) else None


class LocalStorage(Storage):
    name = "local"

    def __init__(self, root: Path | None = None):
        self._root = root

    @property
    def root(self) -> Path:
        return Path(self._root) if self._root else data_root()

    def _p(self, key: str) -> Path:
        return self.root / _check_key(key)

    def put_file(self, key, local, content_type=None):
        dest = self._p(key)
        if dest.exists() and Path(local).resolve() == dest.resolve():
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(local, dest)

    def get_to_file(self, key, local):
        src = self._p(key)
        if not src.is_file():
            return False
        if Path(local).resolve() != src.resolve():
            Path(local).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, local)
        return True

    def exists(self, key):
        return self._p(key).is_file()

    def delete(self, key):
        p = self._p(key)
        if p.is_file():
            p.unlink()
            return True
        return False

    def delete_prefix(self, prefix):
        p = self._p(prefix.rstrip("/"))
        if p.is_dir():
            n = sum(1 for x in p.rglob("*") if x.is_file())
            shutil.rmtree(p, ignore_errors=True)
            return n
        return 1 if self.delete(prefix) else 0

    def list(self, prefix=""):
        base = self.root / prefix.rstrip("/") if prefix else self.root
        if base.is_file():
            return [prefix]
        return sorted(x.relative_to(self.root).as_posix() for x in base.rglob("*") if x.is_file()) if base.is_dir() else []


class S3Storage(Storage):
    name = "s3"
    remote = True

    def __init__(self, bucket: str, prefix: str = "", client=None, endpoint: str | None = None, region: str | None = None):
        if not bucket:
            raise RuntimeError("MIRAGE_S3_BUCKET is required for MIRAGE_STORAGE=s3")
        self.bucket = bucket
        self.prefix = prefix.strip("/") + "/" if prefix.strip("/") else ""
        if client is None:
            try:
                import boto3  # optional dependency
                from botocore.config import Config
            except ImportError as e:
                raise RuntimeError("MIRAGE_STORAGE=s3 needs boto3: pip install boto3") from e
            client = boto3.client("s3", endpoint_url=endpoint or None, region_name=region or None,
                                  config=Config(signature_version="s3v4", s3={"addressing_style": "path" if endpoint else "auto"}))
        self.c = client

    def _k(self, key: str) -> str:
        return self.prefix + _check_key(key)

    def put_file(self, key, local, content_type=None):
        self.c.upload_file(str(local), self.bucket, self._k(key), ExtraArgs={"ContentType": _ctype(key, content_type)})

    def get_to_file(self, key, local):
        Path(local).parent.mkdir(parents=True, exist_ok=True)
        tmp = Path(str(local) + ".part")
        try:
            self.c.download_file(self.bucket, self._k(key), str(tmp))
        except Exception as e:  # noqa: BLE001 - botocore ClientError 404 / NoSuchKey
            tmp.unlink(missing_ok=True)
            if _is_missing(e):
                return False
            raise
        tmp.replace(local)
        return True

    def exists(self, key):
        try:
            self.c.head_object(Bucket=self.bucket, Key=self._k(key))
            return True
        except Exception as e:  # noqa: BLE001
            if _is_missing(e):
                return False
            raise

    def delete(self, key):
        existed = self.exists(key)
        self.c.delete_object(Bucket=self.bucket, Key=self._k(key))
        return existed

    def list(self, prefix=""):
        out, token = [], None
        while True:
            kw = {"Bucket": self.bucket, "Prefix": self.prefix + prefix}
            if token:
                kw["ContinuationToken"] = token
            r = self.c.list_objects_v2(**kw)
            out += [o["Key"][len(self.prefix):] for o in r.get("Contents", [])]
            if not r.get("IsTruncated"):
                return sorted(out)
            token = r["NextContinuationToken"]

    def delete_prefix(self, prefix):
        keys = self.list(prefix.rstrip("/") + "/") + ([prefix] if self.exists(prefix) else [])
        for i in range(0, len(keys), 1000):
            self.c.delete_objects(Bucket=self.bucket, Delete={"Objects": [{"Key": self.prefix + k} for k in keys[i:i + 1000]]})
        return len(keys)

    def presign(self, key, ttl=300, content_type=None):
        params = {"Bucket": self.bucket, "Key": self._k(key)}
        if content_type or Path(key).suffix:
            params["ResponseContentType"] = _ctype(key, content_type)
        return self.c.generate_presigned_url("get_object", Params=params, ExpiresIn=int(ttl))


def _is_missing(e: Exception) -> bool:
    code = str(getattr(e, "response", {}).get("Error", {}).get("Code", ""))
    return code in ("404", "NoSuchKey", "NotFound") or "404" in str(e)[:60]


# ---------------- selection ----------------
_cache: dict[tuple, Storage] = {}
_override: Optional[Storage] = None


def set_storage(s: Optional[Storage]) -> None:
    """Tests / embedders: force a backend (None restores env-driven selection)."""
    global _override
    _override = s


def get_storage() -> Storage:
    if _override is not None:
        return _override
    kind = os.getenv("MIRAGE_STORAGE", "local").strip().lower() or "local"
    cfg = (kind, os.getenv("MIRAGE_S3_BUCKET", ""), os.getenv("MIRAGE_S3_PREFIX", ""), os.getenv("MIRAGE_S3_ENDPOINT", ""),
           os.getenv("MIRAGE_S3_REGION", ""))
    if cfg not in _cache:
        if kind == "local":
            _cache[cfg] = LocalStorage()
        elif kind == "s3":
            _cache[cfg] = S3Storage(cfg[1], cfg[2], endpoint=cfg[3] or None, region=cfg[4] or None)
        else:
            raise RuntimeError(f"unknown MIRAGE_STORAGE={kind!r} (use local or s3)")
    return _cache[cfg]


# ---------------- call-site helpers (all no-ops on the local backend) ----------------
def publish(path: str | Path) -> None:
    """Upload a finished local file (or every file under a directory) to the durable store. Never raises on the local backend."""
    st = get_storage()
    if not st.remote:
        return
    p = Path(path)
    files = [x for x in p.rglob("*") if x.is_file() and not x.name.endswith((".part", ".tmp"))] if p.is_dir() else [p]
    for f in files:
        st.put_file(key_for(f), f)


def ensure_local(path: str | Path) -> bool:
    """Make sure `path` exists locally (downloads from the store when it is missing). False if it exists nowhere."""
    p = Path(path)
    if p.exists():
        return True
    st = get_storage()
    if not st.remote:
        return False
    return st.get_to_file(key_for(p), p)


def exists(path: str | Path) -> bool:
    p = Path(path)
    if p.exists():
        return True
    st = get_storage()
    return st.exists(key_for(p)) if st.remote else False


def remove(path: str | Path) -> int:
    """Delete a file or directory tree from the store (the local working copy is the caller's to remove). -> #objects removed remotely."""
    st = get_storage()
    if not st.remote:
        return 0
    try:
        key = key_for(path)
    except ValueError:
        return 0
    return st.delete_prefix(key) if not Path(path).suffix else (1 if st.delete(key) else 0)


def serve(path: str | Path, media_type: str, headers: Optional[dict] = None):
    """FastAPI response for a stored file: local file -> FileResponse; remote -> redirect to a short presigned URL
    (or stream via a local cache copy when MIRAGE_S3_REDIRECT=0). Raises HTTP 404 when absent."""
    from fastapi import HTTPException
    from fastapi.responses import FileResponse, RedirectResponse

    from . import settings

    p = Path(path)
    st = get_storage()
    if p.exists():
        return FileResponse(p, media_type=media_type, headers=headers)
    if st.remote:
        key = key_for(p)
        if os.getenv("MIRAGE_S3_REDIRECT", "1") != "0":
            if not st.exists(key):
                raise HTTPException(404, "not found")
            return RedirectResponse(st.presign(key, min(settings.signed_url_ttl(), 300), media_type), status_code=307,
                                    headers={"Cache-Control": "no-store"})
        if st.get_to_file(key, p):
            return FileResponse(p, media_type=media_type, headers=headers)
    raise HTTPException(404, "not found")
