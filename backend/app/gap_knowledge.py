"""URL ingestion for the persona knowledge base (Tavus/HeyGen/D-ID all take website sources)."""
from __future__ import annotations

import hashlib
import re
from html.parser import HTMLParser
from typing import Callable

MAX_BYTES = 3 * 1024 * 1024
_SKIP = {"script", "style", "noscript", "svg", "head", "template", "iframe", "nav", "footer"}
_BLOCK = {"p", "div", "br", "li", "tr", "section", "article", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "table", "blockquote"}


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        if tag in _SKIP:
            self._skip += 1
        elif tag in _BLOCK:
            self.parts.append("\n")
            if tag[0] == "h" and len(tag) == 2:
                self.parts.append("\n# ")

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag in _SKIP and self._skip:
            self._skip -= 1
        elif tag in _BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> tuple[str, str]:
    """-> (title, readable text). Scripts/styles/nav/footer dropped, headings kept as '# ' lines for the chunker."""
    p = _Text()
    p.feed(html)
    txt = "".join(p.parts)
    txt = re.sub(r"[ \t\r\f\v]+", " ", txt)
    txt = re.sub(r"\n\s*\n\s*\n+", "\n\n", txt)
    return re.sub(r"\s+", " ", p.title).strip(), "\n".join(l.strip() for l in txt.split("\n")).strip()


def _default_fetch(url: str) -> tuple[str, str]:
    """-> (content_type, body text). SSRF-guarded (netguard), size capped, redirects re-validated."""
    from . import netguard

    netguard.check_url(url)
    with netguard.client(follow_redirects=True, timeout=20) as c:
        with c.stream("GET", url, headers={"user-agent": "VocalFaceBot/1.0 (+knowledge ingestion)"}) as r:
            r.raise_for_status()
            buf = bytearray()
            for chunk in r.iter_bytes():
                buf += chunk
                if len(buf) > MAX_BYTES:
                    raise ValueError("page too large (3 MB max)")
            ctype = r.headers.get("content-type", "")
            if "pdf" in ctype or bytes(buf[:5]) == b"%PDF-":
                from .knowledge import extract_pdf_text

                return "application/pdf", extract_pdf_text(bytes(buf))
            return ctype, bytes(buf).decode(r.encoding or "utf-8", errors="replace")


_fetcher: Callable[[str], tuple[str, str]] = _default_fetch


def set_fetcher(f: Callable[[str], tuple[str, str]] | None) -> None:
    """Test hook."""
    global _fetcher
    _fetcher = f or _default_fetch


def fetch_document(url: str) -> tuple[str, str, str]:
    """-> (title, text, content_hash). Raises ValueError with a user-safe message."""
    from . import netguard

    url = url.strip()
    if not re.match(r"^https?://", url, re.I):
        raise ValueError("only http(s) URLs are supported")
    try:
        ctype, body = _fetcher(url)
    except netguard.UnsafeURL as e:
        raise ValueError(str(e))
    except ValueError:
        raise
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"could not fetch the URL: {type(e).__name__}")
    if "html" in ctype.lower() or (not ctype and "<html" in body[:2000].lower()):
        title, text = html_to_text(body)
    else:
        title, text = "", body
    if not text.strip():
        raise ValueError("the page has no readable text")
    return title or url, text, hashlib.sha256(text.encode()).hexdigest()
