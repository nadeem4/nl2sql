"""The link preview: what a pasted link to the playground unfurls into.

Three things have to be true for a card to appear, and each is why a piece of
this module exists:

1. **The tags are in the HTML the server sends.** A crawler runs no
   JavaScript, so a tag the React bundle adds on mount is a tag nobody sees.
   :func:`with_preview` puts them in the ``<head>`` of the built page as it
   goes out.
2. **``og:image`` and ``og:url`` are absolute.** A relative path is not
   resolved by most unfurlers, and the one absolute URL that would work
   everywhere does not exist: the same page is the Space, a container, and
   ``http://127.0.0.1:8000``. :func:`origin` reads the host the visitor typed
   off the request instead.
3. **The image is served by the app.** The card ships in the package, so a
   ``pip install "nl2sql-engine[demo]"`` serves it too; ``docs/assets`` holds
   the same bytes for the Space's own card, which is read from GitHub before
   the Space has built.

The card itself is generated: ``scripts/social_card.html`` is the source and
``python scripts/render_social_card.py`` renders it.
"""

from __future__ import annotations

import html
import pathlib
import re
from importlib.resources import files

CARD = pathlib.Path(str(files("nl2sql.cli.demo.playground") / "assets" / "social-card.png"))

# Where the app serves the card. A path of its own rather than one under
# ``/static``, because the build empties that directory on every `npm run
# build` and the card is not the bundle's to write.
CARD_ROUTE = "/social-card.png"

TITLE = "nl2sql playground"

# The card says the same thing in the same words: whatever is changed here is
# changed in `scripts/social_card.html` too. ``—`` is an em dash; the rest
# of this repository's source is ASCII and this keeps it that way.
DESCRIPTION = (
    "Ask a database in plain English — the model plans, the code writes the SQL. "
    "Bring your own API key and ask the sample databases: the plan, the checks, "
    "the SQL, the rows and the cost, step by step."
)

# A host is a name and an optional port and nothing else. Anything else is
# either a proxy this code does not understand or a forged header trying to
# write a URL of its own into the page, and both fall back.
_HOST = re.compile(r"[A-Za-z0-9.\-]{1,253}(:[0-9]{1,5})?")


def _first(value: str | None) -> str | None:
    """The first entry of a forwarded header; proxies chain them with commas."""
    return value.split(",")[0].strip() if value else None


def origin(request) -> str:
    """The scheme and host to build the preview's absolute URLs from.

    A Space serves this page behind a proxy: the request the app sees is plain
    HTTP to an internal address, while the visitor typed
    ``https://nadeem4nk-nl2sql-demo.hf.space``. The forwarded headers carry
    that, so they decide; with no proxy in front they are absent and the URL
    the app itself saw is already right.
    """
    scheme = _first(request.headers.get("x-forwarded-proto")) or request.url.scheme
    host = _first(request.headers.get("x-forwarded-host")) or request.headers.get("host") or ""
    if scheme in ("http", "https") and _HOST.fullmatch(host):
        return f"{scheme}://{host}"
    return str(request.base_url).rstrip("/")


def head_tags(site: str) -> str:
    """The preview's ``<head>`` tags, for a page served from ``site``."""
    page, image = f"{site}/", f"{site}{CARD_ROUTE}"
    tags = [
        ("name", "description", DESCRIPTION),
        ("property", "og:type", "website"),
        ("property", "og:site_name", TITLE),
        ("property", "og:title", TITLE),
        ("property", "og:description", DESCRIPTION),
        ("property", "og:url", page),
        ("property", "og:image", image),
        ("property", "og:image:width", "1200"),
        ("property", "og:image:height", "630"),
        ("property", "og:image:alt", "The nl2sql playground card: ask a database in plain English."),
        ("name", "twitter:card", "summary_large_image"),
        ("name", "twitter:title", TITLE),
        ("name", "twitter:description", DESCRIPTION),
        ("name", "twitter:image", image),
    ]
    return "".join(f'<meta {kind}="{key}" content="{html.escape(value, quote=True)}" />'
                   for kind, key, value in tags)


def with_preview(page: str, request) -> str:
    """The built page with the preview tags in its ``<head>``."""
    return page.replace("</head>", head_tags(origin(request)) + "</head>", 1)
