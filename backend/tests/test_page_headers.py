"""Security headers / CSP on the static HTML pages (mic and camera pages)."""
import re

from fastapi.testclient import TestClient

from app.main import app

c = TestClient(app)


def csp(path):
    return c.get(path).headers.get("content-security-policy", "")


def test_static_html_has_closed_csp():
    for path in ("/static/playground.html", "/guest/sh_abc", "/v1/playground"):
        p = csp(path)
        assert "default-src 'self'" in p and "object-src 'none'" in p and "base-uri 'none'" in p, path
        assert "form-action 'none'" in p
        assert re.search(r"connect-src 'self' ws://testserver wss://testserver", p)
        assert "http://" not in p.split("frame-ancestors")[0] and "*" not in p.split("frame-ancestors")[0]


def test_playground_frameable_only_by_dashboard_origins(monkeypatch):
    monkeypatch.setenv("VOCALFACE_CORS_ORIGINS", "https://app.example.com,*")
    for path in ("/static/playground.html", "/v1/playground"):
        r = c.get(path)
        assert "x-frame-options" not in r.headers
        assert r.headers["content-security-policy"].endswith("frame-ancestors 'self' https://app.example.com")


def test_guest_and_assets_not_frameable_by_others():
    r = c.get("/guest/sh_abc")
    assert r.headers["x-frame-options"] == "DENY" and "frame-ancestors 'none'" in r.headers["content-security-policy"]
    j = c.get("/static/vocalface-client.js")  # scripts do not need a page CSP
    assert "content-security-policy" not in j.headers and j.headers["x-content-type-options"] == "nosniff"


def test_host_header_cannot_inject_into_csp():
    p = c.get("/static/playground.html", headers={"host": "evil.com; script-src *"}).headers["content-security-policy"]
    assert "*" not in p.split("frame-ancestors")[0] and p.count("; script-src ") == 1 and ";" not in p.split("connect-src")[1].split(";")[0]


def test_static_pages_have_no_external_resources():
    from pathlib import Path
    for f in (Path(__file__).resolve().parents[1] / "app" / "static").glob("*.html"):
        assert not re.search(r"""(src|href)=["']https?://""", f.read_text()), f.name
