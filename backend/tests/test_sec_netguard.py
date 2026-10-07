"""SSRF guard (netguard.py): every server-side fetch of a user-supplied URL."""
import socket

import httpx
import pytest

from app import consent_verify, jobs, netguard, webhooks


def fake_dns(monkeypatch, table: dict, calls: list | None = None):
    def gai(host, port, *a, **k):
        if calls is not None:
            calls.append(host)
        v = table.get(host)
        if v is None:
            try:
                socket.inet_pton(socket.AF_INET, host)
                v = [host]
            except OSError:
                try:
                    socket.inet_pton(socket.AF_INET6, host)
                    v = [host]
                except OSError:
                    raise socket.gaierror("no such host")
        if callable(v):
            v = v()
        return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port or 0)) for ip in v]
    monkeypatch.setattr(socket, "getaddrinfo", gai)


@pytest.fixture()
def prod_block(monkeypatch):
    monkeypatch.setenv("VOCALFACE_BLOCK_PRIVATE_URLS", "1")
    fake_dns(monkeypatch, {"public.example": ["93.184.216.34"], "evil.example": ["10.0.0.5"],
                           "mixed.example": ["93.184.216.34", "127.0.0.1"], "meta.example": ["169.254.169.254"]})


BAD = ["http://127.0.0.1/", "http://localhost:8000/", "http://10.1.2.3/x", "http://192.168.0.10/", "http://172.16.5.5/",
       "http://169.254.169.254/latest/meta-data/", "http://100.64.0.1/", "http://0.0.0.0/", "http://[::1]/",
       "http://[::ffff:127.0.0.1]/", "http://[fd00::1]/", "http://[fe80::1]/", "http://evil.example/", "http://mixed.example/",
       "http://meta.example/", "http://224.0.0.1/", "http://240.0.0.1/"]


@pytest.mark.parametrize("url", BAD)
def test_private_targets_refused(prod_block, url, monkeypatch):
    if "localhost" in url:
        fake_dns(monkeypatch, {"localhost": ["127.0.0.1"]})
    with pytest.raises(netguard.UnsafeURL):
        netguard.check_url(url)


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://public.example/x", "gopher://public.example/", "//public.example/x"])
def test_non_http_schemes_refused(prod_block, url):
    with pytest.raises(netguard.UnsafeURL):
        netguard.check_url(url)


def test_public_url_allowed_and_unresolvable_refused(prod_block):
    netguard.check_url("https://public.example/video.mp4")
    with pytest.raises(netguard.UnsafeURL):
        netguard.check_url("https://does-not-resolve.example/")


def test_default_policy_dev_allows_localhost_production_blocks(monkeypatch):
    monkeypatch.delenv("VOCALFACE_BLOCK_PRIVATE_URLS", raising=False)
    monkeypatch.delenv("VOCALFACE_ALLOW_PRIVATE_URLS", raising=False)
    monkeypatch.setenv("VOCALFACE_ENV", "dev")
    assert not netguard.blocking()
    netguard.check_url("http://127.0.0.1:9/")  # dev tests use local servers
    monkeypatch.setenv("VOCALFACE_ENV", "production")
    assert netguard.blocking()
    with pytest.raises(netguard.UnsafeURL):
        netguard.check_url("http://127.0.0.1:9/")
    monkeypatch.setenv("VOCALFACE_BLOCK_PRIVATE_URLS", "0")  # explicit operator override
    assert not netguard.blocking()
    monkeypatch.setenv("VOCALFACE_ENV", "dev")
    monkeypatch.setenv("VOCALFACE_BLOCK_PRIVATE_URLS", "1")  # dev can opt in
    assert netguard.blocking()


def test_redirect_to_private_is_blocked_hop_by_hop(prod_block):
    seen = []

    def handler(req: httpx.Request):
        seen.append(str(req.url))
        if req.headers["host"] == "public.example":
            return httpx.Response(302, headers={"location": "http://evil.example/secret"})
        return httpx.Response(200, text="SECRET")

    with netguard.client(inner=httpx.MockTransport(handler), follow_redirects=True) as c:
        with pytest.raises(netguard.UnsafeURL):
            c.get("http://public.example/start")
    assert seen == ["http://93.184.216.34/start"]  # the private hop never reached the network layer


def test_relative_redirect_keeps_real_host_and_connection_is_pinned(prod_block):
    seen = []

    def handler(req: httpx.Request):
        seen.append((str(req.url), req.headers["host"], req.extensions.get("sni_hostname")))
        if req.url.path == "/a":
            return httpx.Response(302, headers={"location": "/b"})
        return httpx.Response(200, text="ok")

    with netguard.client(inner=httpx.MockTransport(handler), follow_redirects=True) as c:
        assert c.get("https://public.example/a").text == "ok"
    assert [x[0] for x in seen] == ["https://93.184.216.34/a", "https://93.184.216.34/b"]
    assert all(x[1] == "public.example" and x[2] == "public.example" for x in seen)


def test_dns_rebinding_cannot_swap_the_address_after_the_check(monkeypatch):
    """The validated IP is the one connected to: a second (malicious) DNS answer is never used."""
    monkeypatch.setenv("VOCALFACE_BLOCK_PRIVATE_URLS", "1")
    answers = iter([["93.184.216.34"], ["127.0.0.1"], ["127.0.0.1"]])
    fake_dns(monkeypatch, {"rebind.example": lambda: next(answers)})
    hosts = []

    def handler(req):
        hosts.append(req.url.host)
        return httpx.Response(200, text="ok")

    with netguard.client(inner=httpx.MockTransport(handler)) as c:
        c.get("http://rebind.example/")
    assert hosts == ["93.184.216.34"]  # one resolution, pinned


def test_async_client_blocks_too(prod_block):
    import asyncio

    async def go():
        async with netguard.async_client(inner=httpx.MockTransport(lambda r: httpx.Response(200))) as c:
            await c.get("http://meta.example/latest")

    with pytest.raises(netguard.UnsafeURL):
        asyncio.run(go())


def test_download_size_cap_and_cleanup(prod_block, tmp_path, monkeypatch):
    monkeypatch.setattr(netguard.httpx, "HTTPTransport", lambda *a, **k: httpx.MockTransport(lambda r: httpx.Response(200, content=b"x" * 5000)))
    dest = tmp_path / "v.mp4"
    with pytest.raises(ValueError, match="too large"):
        netguard.download("http://public.example/v.mp4", dest, 1000)
    assert not dest.exists()
    netguard.download("http://public.example/v.mp4", dest, 10_000)
    assert dest.stat().st_size == 5000


# ---------------- every call site ----------------
def test_training_video_download_refuses_metadata(prod_block, tmp_path):
    with pytest.raises(ValueError, match="refused"):
        consent_verify.fetch_train_video("http://169.254.169.254/latest/meta-data/", tmp_path / "x")


def test_worker_downloader_refuses_private_hosts_and_prod_local_paths(prod_block, tmp_path, monkeypatch):
    with pytest.raises(netguard.UnsafeURL):
        jobs.fetch_video("http://10.0.0.7/video.mp4", tmp_path / "x.mp4")
    monkeypatch.setenv("VOCALFACE_ENV", "production")
    with pytest.raises(ValueError, match="only http"):
        jobs.fetch_video("/etc/hosts", tmp_path / "y.mp4")


def test_webhook_url_validation_and_delivery_refuse_private(prod_block):
    for u in ("http://169.254.169.254/hook", "http://localhost/hook", "http://evil.example/h"):
        with pytest.raises(ValueError):
            webhooks.validate_url(u)
    webhooks.validate_url("https://public.example/hook")
    with pytest.raises(netguard.UnsafeURL):  # delivery re-checks (the stored URL may have been fine at creation time)
        webhooks.http_send("http://evil.example/h", {}, "{}")


def test_tool_webhook_and_job_callback_refuse_private(prod_block):
    from app import llm_backends

    with pytest.raises(netguard.UnsafeURL):
        llm_backends._http_post("http://169.254.169.254/x", "{}", {}, 2)
    assert jobs.default_webhook("http://evil.example/cb", {"a": 1}).startswith("failed:")


def test_custom_llm_base_url_refuses_private(prod_block):
    import asyncio

    from app import llm_backends

    async def go():
        b = llm_backends.OpenAIBackend("http://169.254.169.254/v1", "m")
        async for _ in b.chat([{"role": "user", "content": "hi"}]):
            pass

    with pytest.raises(Exception) as ei:
        asyncio.run(go())
    assert "non-public" in str(ei.value) or "private" in str(ei.value)


def test_photo_url_and_listening_clip_refuse_private(prod_block, monkeypatch):
    from fastapi.testclient import TestClient
    from sqlalchemy.pool import StaticPool
    from sqlmodel import Session, SQLModel, create_engine

    from app import db
    from app.main import app

    monkeypatch.setattr(db, "engine", create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool))
    SQLModel.metadata.create_all(db.engine)
    monkeypatch.setenv("VOCALFACE_SECRET_KEY", "test-secret-key-0123456789")

    def sess():
        with Session(db.engine) as s:
            yield s

    app.dependency_overrides[db.get_session] = sess
    try:
        c = TestClient(app)
        h = {"x-api-key": c.post("/v1/signup", json={"email": "a@b.co"}).json()["api_key"]}
        r = c.post("/v1/replicas/photo", json={"name": "p", "photo_url": "http://169.254.169.254/p.png"}, headers=h)
        assert r.status_code == 422 and "refused" in r.text
        ok = c.post("/v1/replicas/photo", json={"name": "p", "photo_url": "http://public.example/p.png"}, headers=h)
        assert ok.status_code == 200
        r = c.post(f"/v1/replicas/{ok.json()['id']}/listening-clip", json={"url": "http://10.0.0.1/clip.mp4"}, headers=h)
        assert r.status_code in (422, 404), r.text  # refused (not downloaded)
    finally:
        app.dependency_overrides.clear()


def test_pinned_connection_over_a_real_socket_keeps_the_host_header(monkeypatch):
    """Real TCP: hostname resolves (fake DNS) to loopback, loopback is allowed ONLY for this test; the server must see
    the original Host header although the socket was opened to the validated IP."""
    import http.server
    import threading

    got = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            got["host"] = self.headers["Host"]
            self.send_response(200); self.send_header("content-length", "2"); self.end_headers(); self.wfile.write(b"ok")

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    monkeypatch.setenv("VOCALFACE_BLOCK_PRIVATE_URLS", "1")
    fake_dns(monkeypatch, {"pin.example": ["127.0.0.1"]})
    with pytest.raises(netguard.UnsafeURL):  # normally refused
        netguard.get(f"http://pin.example:{port}/")
    monkeypatch.setattr(netguard, "_bad_ip", lambda ip: False)
    try:
        assert netguard.get(f"http://pin.example:{port}/").text == "ok"
    finally:
        srv.shutdown()
    assert got["host"] == f"pin.example:{port}"
