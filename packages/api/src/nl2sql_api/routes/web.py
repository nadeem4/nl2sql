"""The web app: the built page, its chunks, the clips and the social card (docs/architecture/wave_d.md, "nl2sql-api serve").

None of these is an API route: they are served at ``/`` and left out of the OpenAPI document. Any GET that is not a
file or a route answers the page (:func:`install`), so the page's hash router owns navigation; an unknown path under ``/api/`` is the
error envelope, never the page.
"""
import pathlib
import re
from typing import Optional

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, Response

from starlette.exceptions import HTTPException as StarletteHTTPException

from nl2sql_api.errors import RequestRefused
from nl2sql_api.preview import CARD_ROUTE, with_preview

router = APIRouter(tags=["web"], include_in_schema=False)

WEB_DIR = pathlib.Path(__file__).resolve().parents[1] / "web"
# The built page and its hashed chunks (nl2sql-ui, `npm run build`); the folder is not committed.
STATIC_DIR = WEB_DIR / "static"
# What is not the build's to write: the home page's clips (a light and a dark webm and poster per feature, recorded by
# scripts/record_home_clips.py) and the social card. Both are committed.
ASSETS_DIR = WEB_DIR / "assets"

CLIP_NAME = re.compile(r"^[a-z]+(-dark)?\.(webm|jpg)$")
CLIP_TYPES = {"webm": "video/webm", "jpg": "image/jpeg"}

_MISSING_PAGE = (
    "<!doctype html><html><head><title>nl2sql</title></head><body><div id=\"root\">The web app has not been built. "
    "Run <code>npm install &amp;&amp; npm run build</code> in <code>nl2sql-ui</code>.</div></body></html>"
)


def _page(request: Request) -> Response:
    try:
        page = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    except OSError:
        return HTMLResponse(_MISSING_PAGE, status_code=503)
    # The link preview is the hosted demo's: its tags carry absolute URLs, so they are added per request.
    if getattr(request.app.state, "hosted", None) is not None:
        page = with_preview(page, request)
    return HTMLResponse(page)


def _file_in(root: pathlib.Path, relative: str):
    """``relative`` under ``root`` when it names a file there, else None: no path leads outside ``root``."""
    base = root.resolve()
    path = (base / relative).resolve()
    return path if path.is_relative_to(base) and path.is_file() else None


@router.get(CARD_ROUTE)
def social_card() -> FileResponse:
    card = _file_in(ASSETS_DIR, "social-card.png")
    if card is None:
        raise RequestRefused(404, "NOT_FOUND", "No preview card in this install.")
    return FileResponse(card, media_type="image/png")


@router.get("/clips/{name}")
def clip(name: str) -> FileResponse:
    """One of the home page's clips. The name is a plain token, so no path can lead outside the clips folder."""
    path = _file_in(ASSETS_DIR / "clips", name) if CLIP_NAME.match(name) else None
    if path is None:
        raise RequestRefused(404, "NOT_FOUND", "No such clip.")
    return FileResponse(path, media_type=CLIP_TYPES[name.rsplit(".", 1)[1]])


def fallback(request: Request) -> Optional[Response]:
    """A file of the build (the chunks under ``/assets/``, anything else it wrote), else the page."""
    path = request.url.path.lstrip("/")
    found = _file_in(STATIC_DIR, path) if path else None
    if found is not None and found.name != "index.html":
        return FileResponse(found)
    if path.startswith("assets/"):
        return None  # a missing chunk is a 404, never the page
    return _page(request)


def install(app: FastAPI) -> None:
    """Answer the page for a GET that matches no route. It is a 404 handler rather than a catch-all route so that a route
    added after the app was built is still found first; ``/api/...`` and ``/assets/...`` keep the 404 envelope."""
    other = app.exception_handlers[StarletteHTTPException]

    async def not_found_is_the_page(request: Request, exc: StarletteHTTPException) -> Response:
        path = request.url.path
        if (exc.status_code == 404 and request.method == "GET" and exc.detail == "Not Found"
                and not (path == "/api" or path.startswith("/api/"))):
            response = fallback(request)
            if response is not None:
                return response
        return other(request, exc)

    app.add_exception_handler(StarletteHTTPException, not_found_is_the_page)
