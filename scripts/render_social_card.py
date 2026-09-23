"""Renders ``scripts/social_card.html`` into the link preview card.

The card is the 1200x630 image a link to the hosted demo unfurls into: the
playground's Open Graph tags name it, and the Hugging Face Space's front-matter
points its ``thumbnail`` at the copy in ``docs/``.

Two copies are written, and a test holds them byte-identical:

``docs/assets/social-card.png``
    what the Space's ``thumbnail:`` reads over raw.githubusercontent.com, so
    the Space card works before the Space has built, and what the docs show.

``packages/nl2sql/src/nl2sql/cli/demo/playground/assets/social-card.png``
    what the playground itself serves at ``/social-card.png``. It ships in the
    wheel, so ``pip install "nl2sql-engine[demo]"`` has it too.

The card borrows the playground's bundled fonts, so run ``npm ci`` in
``web/playground`` first, then::

    python scripts/render_social_card.py

Headless Chrome does the rendering. The fonts are inlined as ``data:`` URIs
into a copy of the page first, because Chrome refuses to load a font from a
``file:`` URL into a ``file:`` page. Chrome is found on PATH, in the usual
Windows and macOS locations, or named with ``--chrome``.
"""

from __future__ import annotations

import argparse
import base64
import mimetypes
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts" / "social_card.html"
OUTPUTS = (
    ROOT / "docs" / "assets" / "social-card.png",
    ROOT / "packages" / "nl2sql" / "src" / "nl2sql" / "cli" / "demo" / "playground" / "assets" / "social-card.png",
)
WIDTH, HEIGHT = 1200, 630

# Every local file the stylesheet reaches for, so none is left to Chrome.
_URL = re.compile(r"""url\(\s*["']?([^"')]+)["']?\s*\)""")

_CHROME_CANDIDATES = (
    "chrome", "google-chrome", "chromium", "chromium-browser", "msedge",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)


def find_chrome(named: str | None) -> str:
    if named:
        return named
    for candidate in _CHROME_CANDIDATES:
        found = shutil.which(candidate) or (candidate if pathlib.Path(candidate).exists() else None)
        if found:
            return found
    raise SystemExit("No Chrome found. Pass one with --chrome.")


def inline_assets(html: str, base: pathlib.Path) -> str:
    """Replaces every ``url(...)`` pointing at a local file with a data URI."""

    def replace(match: re.Match[str]) -> str:
        reference = match.group(1)
        if reference.startswith(("data:", "http:", "https:")):
            return match.group(0)
        path = (base / reference).resolve()
        if not path.is_file():
            raise SystemExit(f"{reference} is missing. Run `npm ci` in web/playground first.")
        media = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return f'url("data:{media};base64,{base64.b64encode(path.read_bytes()).decode("ascii")}")'

    return _URL.sub(replace, html)


def render(chrome: str, html: str) -> bytes:
    with tempfile.TemporaryDirectory() as work:
        page = pathlib.Path(work) / "card.html"
        page.write_text(html, encoding="utf-8")
        shot = pathlib.Path(work) / "card.png"
        subprocess.run(
            [chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars",
             "--force-device-scale-factor=1", f"--window-size={WIDTH},{HEIGHT}",
             "--virtual-time-budget=4000", f"--screenshot={shot}", page.as_uri()],
            check=True, capture_output=True,
        )
        if not shot.is_file():
            raise SystemExit("Chrome wrote no screenshot.")
        return shot.read_bytes()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--chrome", help="the Chrome or Chromium binary to render with")
    args = parser.parse_args(argv)

    png = render(find_chrome(args.chrome), inline_assets(SOURCE.read_text(encoding="utf-8"), SOURCE.parent))
    for out in OUTPUTS:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(png)
        print(f"{out.relative_to(ROOT)}  {len(png) / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
