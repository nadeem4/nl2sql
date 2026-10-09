"""Records the playground home page's clips: one per feature, light and dark.

The home page (``web/playground/src/Home.jsx``) shows a short clip of each page
it describes -- Ask, Pipeline, Retrieval. This script boots the demo in hosted
mode with no key at all, drives each page in headless Chromium, and writes a
muted webm and a poster jpg per feature and theme into
``packages/nl2sql/src/nl2sql/cli/demo/playground/assets/clips/``::

    ask.webm  ask-dark.webm  ask.jpg  ask-dark.jpg  pipeline...  retrieval...

From the repository root, with the demo extra and Playwright installed::

    pip install playwright && python -m playwright install chromium
    python scripts/record_home_clips.py

It never needs a key and never calls a model. The hosted demo answers a
keyless guided question from the recordings the engine ships
(``scripts/record_demo_answers.py``), so:

- **Ask** is recorded only when a guided question has a recording. Until then
  it gets a "Clip coming" poster in both themes and no video, and the page
  shows the poster alone.
- **Pipeline** and **Retrieval** need no model at all (the step list, and a
  search of the index with the local embedder), so they are always real.

``--url`` drives a playground that is already running instead of booting one.
The ``Record home clips`` job in ``.github/workflows/record_demo.yml`` runs the
same command in CI and opens a pull request with the result.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from typing import Dict, List, Optional

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "packages" / "nl2sql" / "src" / "nl2sql" / "cli" / "demo" / "playground" / "assets" / "clips"
FEATURES = ("ask", "pipeline", "retrieval")
THEMES = ("light", "dark")
# 16:10, the frame the home page shows a clip in. Small enough to keep each
# webm well under 2 MB.
SIZE = (1280, 800)
RETRIEVAL_QUERY = "customers by country and their invoices"

# Porcelain's own colours, so a placeholder looks like the page it sits in.
PALETTE = {
    "light": {"paper": "#f5f5f4", "ink": "#141414", "ink2": "#4b4b4b", "accent": "#8c1d2e", "rule": "#d5d5d3"},
    "dark": {"paper": "#161616", "ink": "#ededed", "ink2": "#b0b0b0", "accent": "#f08a9b", "rule": "#2f2f2f"},
}
LABELS = {"ask": "Ask", "pipeline": "Pipeline", "retrieval": "Retrieval"}


def clip_names(feature: str, theme: str) -> Dict[str, str]:
    """The two files one feature writes in one theme."""
    suffix = "" if theme == "light" else "-dark"
    return {"video": f"{feature}{suffix}.webm", "poster": f"{feature}{suffix}.jpg"}


def sample_question(meta: dict) -> Optional[str]:
    """The guided question the Ask clip asks: the first with a recorded answer."""
    recorded = set(meta.get("recorded") or [])
    for group in meta.get("question_groups") or []:
        for question in group.get("questions") or []:
            if question in recorded:
                return question
    return None


def plan_clips(meta: dict) -> Dict[str, str]:
    """``"record"`` or ``"placeholder"`` per feature.

    Ask shows a model's answer, so it is recorded only from a real recording;
    without one there is nothing true to show. The others call no model.
    """
    return {feature: ("record" if feature != "ask" or sample_question(meta) else "placeholder")
            for feature in FEATURES}


def placeholder_html(feature: str, theme: str) -> str:
    """A poster that says plainly the clip has not been recorded yet."""
    c = PALETTE[theme]
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
html,body{{margin:0;height:100%;background:{c['paper']};color:{c['ink']};
font:15px/1.5 "Schibsted Grotesk",system-ui,-apple-system,"Segoe UI",sans-serif}}
main{{height:100%;display:grid;place-content:center;justify-items:start;gap:14px;padding:0 120px}}
p{{margin:0}} .k{{font-size:18px;font-weight:650;color:{c['accent']}}}
h1{{margin:0;font-size:56px;letter-spacing:-.03em;line-height:1.05}}
.s{{font-size:22px;color:{c['ink2']};max-width:34ch}}
hr{{width:96px;border:0;border-top:2px solid {c['rule']};margin:6px 0}}
</style></head><body><main>
<p class="k">{LABELS[feature]}</p><h1>Clip coming</h1><hr>
<p class="s">This one is recorded from a real run once the demo's recorded answers ship.</p>
</main></body></html>"""


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=5) as response:
        return json.loads(response.read())


def boot_demo(directory: pathlib.Path, port: int) -> subprocess.Popen:
    """``nl2sql demo --hosted`` with no key anywhere in its environment."""
    from nl2sql.llm.providers import PROVIDER_KEYS

    env = {k: v for k, v in os.environ.items() if k not in PROVIDER_KEYS}
    env.update({"NL2SQL_DEMO_HOSTED": "1", "EMBEDDING_PROVIDER": "local"})
    command = [sys.executable, "-m", "nl2sql.cli.main", "demo", "--dir", str(directory),
               "--port", str(port), "--no-browser"]
    return subprocess.Popen(command, env=env)


def wait_until_up(base: str, timeout: float = 600.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            return _get_json(base + "api/meta")
        except Exception:
            time.sleep(1.0)
    raise SystemExit(f"The playground at {base} did not come up within {int(timeout)}s.")


def _drive(page, feature: str, base: str, meta: dict, snap) -> None:
    """What each clip shows. ``snap`` takes the poster, at the moment that says the most."""
    if feature == "ask":
        page.goto(base + "#/ask")
        page.wait_for_selector("#question")
        page.wait_for_timeout(600)
        page.click("#question")
        page.keyboard.type(sample_question(meta), delay=28)
        page.wait_for_timeout(300)
        page.click("#ask")
        page.wait_for_selector("#recorded-badge", timeout=120_000)
        page.wait_for_timeout(900)
        snap()
        page.mouse.wheel(0, 520)
        page.wait_for_timeout(1400)
        page.mouse.wheel(0, 520)
        page.wait_for_timeout(1400)
    elif feature == "pipeline":
        page.goto(base + "#/pipeline")
        page.wait_for_selector("#pipeline-panel .psteps")
        page.wait_for_timeout(1200)
        snap()
        for _ in range(3):
            page.mouse.wheel(0, 420)
            page.wait_for_timeout(1000)
    else:
        page.goto(base + "#/retrieval")
        page.wait_for_selector("#retrieval-query")
        page.wait_for_timeout(600)
        page.click("#retrieval-query")
        page.keyboard.type(RETRIEVAL_QUERY, delay=28)
        page.click("#retrieval-search")
        page.wait_for_selector("#retrieval-result", timeout=60_000)
        page.wait_for_timeout(1200)
        page.mouse.wheel(0, 260)
        page.wait_for_timeout(700)
        snap()
        page.mouse.wheel(0, 480)
        page.wait_for_timeout(1400)


def record(base: str, out: pathlib.Path, meta: dict, features: List[str]) -> Dict[str, str]:
    from playwright.sync_api import sync_playwright

    plan = plan_clips(meta)
    done: Dict[str, str] = {}
    out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p, tempfile.TemporaryDirectory() as videos:
        browser = p.chromium.launch()
        for feature in features:
            for theme in THEMES:
                names = clip_names(feature, theme)
                if plan[feature] == "placeholder":
                    page = browser.new_page(viewport={"width": SIZE[0], "height": SIZE[1]}, color_scheme=theme)
                    page.set_content(placeholder_html(feature, theme))
                    page.screenshot(path=str(out / names["poster"]), type="jpeg", quality=82)
                    page.close()
                    (out / names["video"]).unlink(missing_ok=True)
                    continue
                context = browser.new_context(viewport={"width": SIZE[0], "height": SIZE[1]}, color_scheme=theme,
                                              record_video_dir=videos,
                                              record_video_size={"width": SIZE[0], "height": SIZE[1]})
                page = context.new_page()
                poster = str(out / names["poster"])
                _drive(page, feature, base, meta,
                       lambda: page.screenshot(path=poster, type="jpeg", quality=82))
                video = page.video
                context.close()
                shutil.move(video.path(), out / names["video"])
            done[feature] = plan[feature]
        browser.close()
    return done


def _parse(argv: Optional[List[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=pathlib.Path, default=OUT, help="where the clips are written")
    parser.add_argument("--url", default=None, help="a playground already running, instead of booting one")
    parser.add_argument("--dir", type=pathlib.Path, default=None,
                        help="demo project to boot (default: a fresh temporary one)")
    parser.add_argument("--features", default=",".join(FEATURES), help="comma-separated: ask,pipeline,retrieval")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse(argv)
    features = [f for f in args.features.split(",") if f]
    unknown = sorted(set(features) - set(FEATURES))
    if unknown:
        print(f"Unknown features: {', '.join(unknown)}", file=sys.stderr)
        return 2
    server = None
    scratch = None
    base = args.url
    try:
        if base is None:
            directory = args.dir
            if directory is None:
                scratch = tempfile.mkdtemp(prefix="nl2sql-clips-")
                directory = pathlib.Path(scratch) / "demo"
            port = _free_port()
            server = boot_demo(directory, port)
            base = f"http://127.0.0.1:{port}/"
        base = base if base.endswith("/") else base + "/"
        meta = wait_until_up(base)
        done = record(base, args.out, meta, features)
    finally:
        if server is not None:
            server.terminate()
            server.wait(timeout=30)
        if scratch:
            shutil.rmtree(scratch, ignore_errors=True)
    for feature, how in done.items():
        print(f"{feature}: {'recorded' if how == 'record' else 'placeholder poster (no recorded answer yet)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
