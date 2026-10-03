"""SSRF guard for EVERY server-side fetch of a user-supplied URL (training video / photo / listening clip downloads,
webhook delivery, tool webhooks, custom-LLM base_url).

Policy (`blocking()`): MIRAGE_BLOCK_PRIVATE_URLS=1|0 decides; unset -> ON in production, OFF in dev (dev needs localhost
video servers and local test endpoints). When on, a request is refused when ANY address the host resolves to is not a
public unicast address (loopback, RFC1918, link-local incl. the 169.254.169.254 metadata service, CGNAT 100.64/10, ULA,
multicast, reserved, unspecified, IPv4-mapped forms of those) or when the scheme is not http/https.

How it resists the classic bypasses:
* DNS is resolved ONCE per request and the connection is pinned to the validated IP (the original hostname is kept for the
  Host header and TLS SNI/cert verification), so DNS rebinding between "check" and "use" does not work.
* The guard is an httpx transport, so redirects (which httpx performs by calling the transport again) are checked hop by hop.
* Numeric tricks (decimal/octal/hex IPs, IPv6 brackets) are normalised by getaddrinfo before the check.

Use `client()` / `async_client()` or the one-shot helpers `get/post/download`. Operator-configured INTERNAL services
(Ollama, the lip-sync server, LiveKit) deliberately do not go through this module.
"""
from __future__ import annotations

import ipaddress
import os
import socket
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from . import settings


class UnsafeURL(ValueError):
    """The URL points somewhere a user-supplied URL must never reach."""


def blocking() -> bool:
    v = os.getenv("MIRAGE_BLOCK_PRIVATE_URLS", "").strip().lower()
    if v:
        return v in ("1", "true", "yes", "on")
    if os.getenv("MIRAGE_ALLOW_PRIVATE_URLS") == "1":  # legacy escape hatch (consent-time guard)
        return False
    return settings.is_production()


def _bad_ip(ip: ipaddress._BaseAddress) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if isinstance(ip, ipaddress.IPv6Address) and ip.sixtofour is not None:  # 2002::/16 embeds an IPv4 address
        return _bad_ip(ip.sixtofour)
    return (not ip.is_global) or ip.is_multicast or ip.is_unspecified or ip.is_loopback or ip.is_link_local


def resolve_checked(host: str, port: int | None) -> str:
    """Resolve `host` and return ONE validated IP literal to connect to. Raises UnsafeURL."""
    if not host:
        raise UnsafeURL("URL has no host")
    try:
        infos = socket.getaddrinfo(host, port or 80, type=socket.SOCK_STREAM)
    except OSError as e:
        raise UnsafeURL("host does not resolve") from e
    ips = []
    for i in infos:
        ip = ipaddress.ip_address(i[4][0].split("%")[0])
        if _bad_ip(ip):
            raise UnsafeURL("URL points at a private, loopback, link-local or otherwise non-public address")
        ips.append(str(ip))
    if not ips:
        raise UnsafeURL("host does not resolve")
    return ips[0]


def check_url(url: str) -> None:
    """Validate without connecting (use client() when you also want rebinding protection)."""
    sp = urlsplit(url)
    if sp.scheme not in ("http", "https"):
        raise UnsafeURL("only http(s) URLs are allowed")
    if blocking():
        resolve_checked(sp.hostname or "", sp.port)


def _pin(request: httpx.Request) -> httpx.Request:
    url = request.url
    if url.scheme not in ("http", "https"):
        raise UnsafeURL("only http(s) URLs are allowed")
    if not blocking():
        return request
    host = url.host
    ip = resolve_checked(host, url.port)
    if ip == host:
        return request
    # new request object: the original keeps its URL so httpx resolves redirect Locations against the real host.
    # The Host header (already built from the original URL) is kept; TLS SNI + cert check use the original hostname.
    return httpx.Request(request.method, url.copy_with(host=ip), headers=request.headers, stream=request.stream,
                         extensions={**request.extensions, "sni_hostname": host})


class GuardedTransport(httpx.BaseTransport):
    def __init__(self, inner: httpx.BaseTransport | None = None):
        self.inner = inner or httpx.HTTPTransport()

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        return self.inner.handle_request(_pin(request))

    def close(self) -> None:
        self.inner.close()


class AsyncGuardedTransport(httpx.AsyncBaseTransport):
    def __init__(self, inner: httpx.AsyncBaseTransport | None = None):
        self.inner = inner or httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        import asyncio

        request = await asyncio.to_thread(_pin, request)  # getaddrinfo can block
        return await self.inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self.inner.aclose()


def client(*, inner: httpx.BaseTransport | None = None, **kw) -> httpx.Client:
    return httpx.Client(transport=GuardedTransport(inner), **kw)


def async_client(*, inner: httpx.AsyncBaseTransport | None = None, **kw) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=AsyncGuardedTransport(inner), **kw)


def get(url: str, **kw) -> httpx.Response:
    kw.setdefault("timeout", 30)
    with client(follow_redirects=kw.pop("follow_redirects", True)) as c:
        return c.get(url, **kw)


def post(url: str, **kw) -> httpx.Response:
    """POST without following redirects (webhook receivers must not bounce us elsewhere)."""
    kw.setdefault("timeout", 15)
    with client(follow_redirects=False) as c:
        return c.post(url, **kw)


def download(url: str, dest: Path, max_bytes: int, timeout: float = 120, max_redirects: int = 4) -> None:
    """Stream an http(s) URL into `dest`, size capped, every redirect hop validated."""
    got = 0
    with httpx.Client(transport=GuardedTransport(), follow_redirects=True, max_redirects=max_redirects, timeout=timeout) as c:
        try:
            with c.stream("GET", url) as r:
                r.raise_for_status()
                with open(dest, "wb") as f:
                    for chunk in r.iter_bytes():
                        got += len(chunk)
                        if got > max_bytes:
                            raise ValueError("file too large")
                        f.write(chunk)
        except httpx.TooManyRedirects as e:
            raise ValueError("too many redirects") from e
        except Exception:
            Path(dest).unlink(missing_ok=True)
            raise
