from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

router = APIRouter()
PREFIX = ""  # served at /guest/{token}, outside /v1 so the page's inline script is not blocked by the API CSP
STATIC = Path(__file__).resolve().parents[1] / "static"


@router.get("/guest/{token}", include_in_schema=False)
def guest_page(token: str):
    if not token.startswith("sh_") or len(token) > 80:
        raise HTTPException(404, "not found")
    return HTMLResponse((STATIC / "guest.html").read_text(), headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
