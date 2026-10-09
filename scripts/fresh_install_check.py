"""Installs nl2sql the way a user does and boots the demo, with no API key.

What it proves that the unit tests cannot: that the *built* wheels install
into an empty virtualenv with the ``demo`` extra, that the playground page
ships inside the wheel, and that ``nl2sql demo --hosted`` scaffolds the sample
databases, indexes them with the local embedder and serves a page and an API
that answer -- all without a key and without this repository on the path.

Usage::

    python scripts/fresh_install_check.py --build     # build dist/ first, then check
    python scripts/fresh_install_check.py --dist dist # check wheels already built

CI runs it as the ``fresh-install`` job in ``.github/workflows/test.yml``.
The first run downloads the 79 MB local embedding model into
``~/.cache/chroma``, which is shared with any other nl2sql install.

Standard library only: it runs before anything is installed.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Optional

REPO = Path(__file__).resolve().parent.parent
PACKAGES = ("packages/adapter-sdk", "packages/nl2sql", "packages/api")

# Wheel filename stem -> the name this script uses for it. Matched whole, up to
# the `-` before the version: `nl2sql_a*` would match both api and adapter_sdk.
STEMS = {"nl2sql_engine": "engine", "nl2sql_adapter_sdk": "adapter_sdk", "nl2sql_api": "api"}

# The text app.py serves when the React build is missing from the wheel.
UNBUILT_PAGE = "has not been built"

HOST = "127.0.0.1"


# --- which wheels, installed how ----------------------------------------------

def find_wheels(dist: Path) -> Dict[str, Path]:
    """The engine, SDK and API wheels in ``dist``, by short name."""
    found: Dict[str, List[Path]] = {}
    for wheel in sorted(Path(dist).glob("*.whl")):
        stem = wheel.name.split("-", 1)[0]
        if stem in STEMS:
            found.setdefault(STEMS[stem], []).append(wheel)
    for name, wheels in found.items():
        if len(wheels) > 1:
            raise SystemExit(f"{dist} holds more than one {name} wheel: "
                             f"{', '.join(w.name for w in wheels)}. Empty it and build again.")
    for stem, name in STEMS.items():
        if name in ("engine", "adapter_sdk") and name not in found:
            raise SystemExit(f"No {stem} wheel in {dist}. Build with --build, or point --dist at one.")
    return {name: wheels[0] for name, wheels in found.items()}


def wheel_install_args(wheels: Mapping[str, Path], dist: Path) -> List[str]:
    """``pip install`` arguments for the engine with its demo extra, as a user installs it.

    The SDK wheel is named too: the engine pins it to the same version, which
    PyPI does not have until the release is published.
    """
    return ["--find-links", str(dist), f"{wheels['engine']}[demo]", str(wheels["adapter_sdk"])]


# --- the environment the demo runs in -----------------------------------------

def demo_env(environ: Mapping[str, str]) -> Dict[str, str]:
    """``environ`` without any API key, embedding locally.

    Hosted mode refuses to use a key anyway; dropping them here means a
    developer's exported key cannot make a local run pass that CI would fail.
    """
    env = {k: v for k, v in environ.items() if not k.endswith("_API_KEY")}
    env["EMBEDDING_PROVIDER"] = "local"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def demo_command(nl2sql: Path, directory: Path, port: int) -> List[str]:
    """The command the Space's Dockerfile runs, on a loopback port."""
    return [str(nl2sql), "demo", "--hosted", "--dir", str(directory),
            "--host", HOST, "--port", str(port), "--no-browser"]


def venv_executable(venv: Path, name: str, windows: Optional[bool] = None) -> Path:
    windows = os.name == "nt" if windows is None else windows
    return venv / "Scripts" / f"{name}.exe" if windows else venv / "bin" / name


# --- what a healthy playground looks like -------------------------------------

def page_problems(status: int, body: str) -> List[str]:
    problems = []
    if status != 200:
        problems.append(f"GET / answered {status}")
    if 'id="root"' not in body:
        problems.append("GET / has no React root element")
    if UNBUILT_PAGE in body:
        problems.append("GET / serves the placeholder: the built playground is missing from the wheel")
    return problems


def meta_problems(meta: Mapping) -> List[str]:
    problems = []
    if meta.get("hosted") is not True or meta.get("mode") != "hosted":
        problems.append(f"/api/meta is not in hosted mode: mode={meta.get('mode')!r}, hosted={meta.get('hosted')!r}")
    if not meta.get("datasources"):
        problems.append("/api/meta lists no datasources")
    return problems


def schema_problems(schema: Mapping) -> List[str]:
    return [] if schema.get("tables") else ["/api/schema has no tables: the schema was never indexed"]


def index_problems(index: Mapping) -> List[str]:
    health = index.get("health") or {}
    if health.get("status") == "ok":
        return []
    return [f"/api/index reports the vector index {health.get('status')!r}: {health.get('problems')}"]


# --- waiting for the server ---------------------------------------------------

def wait_until_up(answers: Callable[[], bool], alive: Callable[[], bool], timeout: float,
                  clock: Callable[[], float] = time.monotonic,
                  sleep: Callable[[float], None] = time.sleep, interval: float = 2.0) -> None:
    """Returns once ``answers()``; fails if the server exits or ``timeout`` passes."""
    deadline = clock() + timeout
    while True:
        if answers():
            return
        if not alive():
            raise SystemExit("The demo exited before it served the playground.")
        if clock() >= deadline:
            raise SystemExit(f"The demo did not answer within {timeout:.0f}s.")
        sleep(interval)


# --- doing it -----------------------------------------------------------------

def _run(cmd: List[str], **kwargs) -> None:
    print("+ " + " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, **kwargs)


def _get(url: str, timeout: float = 10.0):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return response.status, response.read().decode("utf-8", "replace")


def _answers(url: str) -> Callable[[], bool]:
    def probe() -> bool:
        try:
            return _get(url, timeout=5)[0] == 200
        except (urllib.error.URLError, OSError):
            return False
    return probe


def _free_port() -> int:
    with socket.socket() as s:
        s.bind((HOST, 0))
        return s.getsockname()[1]


def build(dist: Path) -> None:
    if dist.exists():
        shutil.rmtree(dist)
    for package in PACKAGES:
        _run([sys.executable, "-m", "build", str(REPO / package), "--outdir", str(dist)])


def install(venv: Path, args: List[str]) -> Path:
    _run([sys.executable, "-m", "venv", str(venv)])
    python = venv_executable(venv, "python")
    _run([str(python), "-m", "pip", "install", "--quiet", "--upgrade", "pip"])
    _run([str(python), "-m", "pip", "install", *args])
    return venv_executable(venv, "nl2sql")


def check_playground(base: str) -> List[str]:
    problems = page_problems(*_get(base + "/"))
    problems += meta_problems(json.loads(_get(base + "/api/meta")[1]))
    problems += schema_problems(json.loads(_get(base + "/api/schema")[1]))
    problems += index_problems(json.loads(_get(base + "/api/index")[1]))
    return problems


def boot_and_check(nl2sql: Path, work: Path, timeout: float) -> None:
    port = _free_port()
    base = f"http://{HOST}:{port}"
    log_path = work / "demo.log"
    # Run from the work directory, not the repository, so nothing local is
    # importable by accident.
    with open(log_path, "w", encoding="utf-8") as log:
        proc = subprocess.Popen(demo_command(nl2sql, work / "demo", port), cwd=work,
                                env=demo_env(os.environ), stdout=log, stderr=subprocess.STDOUT)
    try:
        wait_until_up(_answers(base + "/api/meta"), alive=lambda: proc.poll() is None, timeout=timeout)
        problems = check_playground(base)
    except BaseException:
        print(log_path.read_text(encoding="utf-8", errors="replace")[-6000:])
        raise
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
    if problems:
        print(log_path.read_text(encoding="utf-8", errors="replace")[-6000:])
        raise SystemExit("The playground is up but unhealthy:\n  - " + "\n  - ".join(problems))
    print(f"OK: a fresh install served the playground, its API and an indexed schema at {base}")


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dist", type=Path, default=REPO / "dist", help="where the wheels are (default: dist/)")
    parser.add_argument("--build", action="store_true", help="build the three distributions into --dist first")
    parser.add_argument("--timeout", type=float, default=900,
                        help="seconds to wait for the demo to serve (default 900; the first run downloads a model)")
    parser.add_argument("--keep", action="store_true", help="keep the virtualenv and demo folder afterwards")
    args = parser.parse_args(argv)

    dist = args.dist.resolve()
    if args.build:
        build(dist)
    install_args = wheel_install_args(find_wheels(dist), dist)

    work = Path(tempfile.mkdtemp(prefix="nl2sql-fresh-install-"))
    try:
        nl2sql = install(work / "venv", install_args)
        boot_and_check(nl2sql, work, args.timeout)
    finally:
        if args.keep:
            print(f"Kept {work}")
        else:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
