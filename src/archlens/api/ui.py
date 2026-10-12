"""The web UI (M4.7): one static page served by the API itself, so it needs no other host and no
CORS. The page holds no data — everything it shows comes from the authenticated routes, with the
API key the user types in. Reports render in a sandboxed `srcdoc` iframe (no scripts, opaque
origin)."""

from pathlib import Path

from fastapi import APIRouter, HTTPException, Response, status

STATIC = Path(__file__).parent / "static"
ASSETS = {
    "app.js": "text/javascript; charset=utf-8",
    "style.css": "text/css; charset=utf-8",
}
# Script and styles from this origin only. The report iframe inherits this policy, hence inline
# styles (the report's own <style>) and data: images, as in the report's CSP.
HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src data:; connect-src 'self'; base-uri 'none'; form-action 'none'; "
        "frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-cache",  # a new image shows the new page at once
}

router = APIRouter(include_in_schema=False)


@router.get("/")
def page() -> Response:
    """The single-page UI."""
    html = (STATIC / "index.html").read_bytes()
    return Response(html, media_type="text/html; charset=utf-8", headers=HEADERS)


@router.get("/ui/{name}")
def asset(name: str) -> Response:
    """The page's script and stylesheet; 404 for any other name."""
    if name not in ASSETS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such file")
    return Response((STATIC / name).read_bytes(), media_type=ASSETS[name], headers=HEADERS)
