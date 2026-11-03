"""Installs nl2sql the way a user does and boots the demo, with no API key.

What it proves that the unit tests cannot: that the *built* wheels install
into an empty virtualenv with the ``demo`` extra (which brings the web app,
``nl2sql-api``), that the web app ships inside the wheel, and that
``nl2sql-api serve --demo --hosted`` scaffolds the sample databases, indexes
them with the local embedder and serves a page and an API that answer, down to
the guided questions and a follow-up -- all without a key and without this
repository on the path.

Usage::

    python scripts/fresh_install_check.py --build     # build dist/ first, then check
    python scripts/fresh_install_check.py --dist dist # check wheels already built
    python scripts/fresh_install_check.py --pypi 0.2.0
        # install a published release from PyPI, retrying while the index catches up
    python scripts/fresh_install_check.py --url https://nadeem4nk-nl2sql-demo.hf.space --expect-version 0.2.0
        # wait for a deployed web app to report that version, then check it

Every mode that installs also checks ``/api/v1/health`` reports the version it
installed, for the engine and the API. CI runs the wheel mode as the ``fresh-install`` job in
``.github/workflows/test.yml``, and the other two after a release, in
``.github/workflows/publish_pypi.yaml``.
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
from typing import Callable, Dict, List, Mapping, Optional, Tuple

REPO = Path(__file__).resolve().parent.parent
PACKAGES = ("packages/sdk", "packages/nl2sql", "packages/api")

# Wheel filename stem -> the name this script uses for it. Matched whole, up to
# the `-` before the version, so one stem never matches another that it prefixes.
STEMS = {"nl2sql_engine": "engine", "nl2sql_sdk": "sdk", "nl2sql_api": "api"}

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
        if name not in found:
            raise SystemExit(f"No {stem} wheel in {dist}. Build with --build, or point --dist at one.")
    return {name: wheels[0] for name, wheels in found.items()}


def wheel_install_args(wheels: Mapping[str, Path], dist: Path) -> List[str]:
    """``pip install`` arguments for the engine with its demo extra, as a user installs it.

    The SDK and API wheels are named too: the engine pins the first and its
    ``demo`` extra the second to the same version, which PyPI does not have
    until the release is published.
    """
    return ["--find-links", str(dist), f"{wheels['engine']}[demo]", str(wheels["sdk"]), str(wheels["api"])]


def wheel_version(wheel: Path) -> str:
    """The version in a wheel's filename: ``name-version-tags.whl``."""
    return Path(wheel).name.split("-")[1]


def pypi_install_args(version: str) -> List[str]:
    """``pip install`` arguments for a published release, exactly as the README says.

    No cache: an index page cached from before the upload is what makes a
    version published a minute ago look missing.
    """
    return ["--no-cache-dir", f"nl2sql-engine[demo]=={version}"]


def retry(action: Callable[[], None], attempts: int, delay: float,
          sleep: Callable[[float], None] = time.sleep) -> None:
    """Runs ``action`` until it stops raising ``CalledProcessError``, at most ``attempts`` times.

    For installing a version PyPI accepted moments ago: the upload is
    immediate, the CDN in front of the index catches up a little later.
    """
    for attempt in range(1, attempts + 1):
        try:
            action()
            return
        except subprocess.CalledProcessError:
            if attempt == attempts:
                raise
            print(f"Attempt {attempt} of {attempts} failed; retrying in {delay:.0f}s.", flush=True)
            sleep(delay)


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


def demo_command(nl2sql_api: Path, directory: Path, port: int) -> List[str]:
    """The command the Space's Dockerfile runs, on a loopback port."""
    return [str(nl2sql_api), "serve", "--demo", "--hosted", "--dir", str(directory),
            "--host", HOST, "--port", str(port)]


def venv_executable(venv: Path, name: str, windows: Optional[bool] = None) -> Path:
    windows = os.name == "nt" if windows is None else windows
    return venv / "Scripts" / f"{name}.exe" if windows else venv / "bin" / name


# --- what a healthy web app looks like ----------------------------------------

def page_problems(status: int, body: str) -> List[str]:
    problems = []
    if status != 200:
        problems.append(f"GET / answered {status}")
    if 'id="root"' not in body:
        problems.append("GET / has no React root element")
    if UNBUILT_PAGE in body:
        problems.append("GET / serves the placeholder: the built web app is missing from the wheel")
    return problems


def recorded_questions(meta: Mapping) -> List[Tuple[str, str]]:
    """``(datasource id, question)`` for each guided question the keyless demo can answer."""
    return [(d["id"], q) for d in meta.get("datasources") or [] for q in d.get("recorded") or []]


def meta_problems(meta: Mapping) -> List[str]:
    problems = []
    if meta.get("mode") != "hosted" or meta.get("start") != "hosted":
        problems.append(f"/api/v1/meta is not in hosted mode: mode={meta.get('mode')!r}, start={meta.get('start')!r}")
    if not meta.get("datasources"):
        problems.append("/api/v1/meta lists no datasources")
    elif not recorded_questions(meta):
        problems.append("/api/v1/meta lists no recorded question: a visitor with no key could ask nothing")
    return problems


def schema_problems(schema: Mapping) -> List[str]:
    return [] if schema.get("tables") else ["the datasource's schema has no tables: it was never indexed"]


def health_problems(health: Mapping, expected: str) -> List[str]:
    problems = []
    if health.get("status") != "ok":
        problems.append(f"/api/v1/health status is {health.get('status')!r}")
    versions = health.get("version") or {}
    for package in ("engine", "api"):
        if versions.get(package) != expected:
            problems.append(f"/api/v1/health reports {package} version {versions.get(package)!r}, not {expected!r}")
    return problems


def index_problems(index: Mapping) -> List[str]:
    if index.get("status") == "ok":
        return []
    return [f"/api/v1/index reports the vector index {index.get('status')!r}: {index.get('problems')}"]


def turn_problems(turn: Mapping, question: str) -> List[str]:
    """A question the demo has a recording for must come back answered, with something to read."""
    if turn.get("status") != "answered" or not (turn.get("reply") or "").strip():
        return [f"{question!r} came back {turn.get('status')!r} with reply {turn.get('reply')!r}, "
                f"error {turn.get('error')!r}"]
    return []


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
            raise SystemExit("The demo exited before it served the web app.")
        if clock() >= deadline:
            raise SystemExit(f"The demo did not answer within {timeout:.0f}s.")
        sleep(interval)


def wait_for_version(health: Callable[[], Optional[Mapping]], expected: str, timeout: float,
                     clock: Callable[[], float] = time.monotonic,
                     sleep: Callable[[float], None] = time.sleep, interval: float = 30.0) -> None:
    """Returns once ``health()`` reports ``expected``; ``None`` means it did not answer.

    A Space goes on serving its previous image while the new one builds, so
    an answer is not enough: it has to be the new version's.
    """
    deadline = clock() + timeout
    last: Optional[Mapping] = None
    while True:
        seen = health()
        if seen is not None:
            last = seen
            if not health_problems(seen, expected):
                return
        if clock() >= deadline:
            what = f"version {last.get('version')!r}" if last else "nothing"
            raise SystemExit(f"Gave up after {timeout:.0f}s waiting for version {expected!r}; "
                             f"the last answer was {what}.")
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


def install(venv: Path, args: List[str], attempts: int = 1, delay: float = 30) -> Path:
    """Installs ``args`` into a new virtualenv; returns its ``nl2sql-api`` executable."""
    _run([sys.executable, "-m", "venv", str(venv)])
    python = venv_executable(venv, "python")
    _run([str(python), "-m", "pip", "install", "--quiet", "--upgrade", "pip"])
    retry(lambda: _run([str(python), "-m", "pip", "install", *args]), attempts=attempts, delay=delay)
    return venv_executable(venv, "nl2sql-api")


class Client:
    """JSON over HTTP with the visitor's session cookie kept, as a browser does."""

    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())

    def get(self, path: str, timeout: float = 30.0) -> Mapping:
        with self.opener.open(self.base + path, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8", "replace"))

    def post(self, path: str, body: Mapping, timeout: float = 300.0) -> Mapping:
        request = urllib.request.Request(self.base + path, data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"}, method="POST")
        # The hosted demo allows a few questions a minute; wait the limit out.
        for attempt in range(12):
            try:
                with self.opener.open(request, timeout=timeout) as response:
                    return json.loads(response.read().decode("utf-8", "replace"))
            except urllib.error.HTTPError as error:
                if error.code != 429 or attempt == 11:
                    raise
                time.sleep(10)


# The scripted follow-up that scripts/record_demo_answers.py records after its question (FOLLOW_UPS there).
FOLLOW_UP = ("Who are the top 5 customers by total spend?", "And the top 10?")


def check_conversation(client: Client, meta: Mapping) -> List[str]:
    """Asks every recorded guided question; after the one that has a recorded follow-up, asks that too. No key."""
    problems: List[str] = []
    for datasource, question in recorded_questions(meta):
        conversation = client.post("/api/v1/conversations", {"datasource_id": datasource})
        path = f"/api/v1/conversations/{conversation['id']}/turns"
        problems += turn_problems(client.post(path, {"text": question}), question)
        if question == FOLLOW_UP[0]:
            problems += turn_problems(client.post(path, {"text": FOLLOW_UP[1]}), FOLLOW_UP[1])
    return problems


def check_queries(client: Client, meta: Mapping) -> List[str]:
    """Asks the advertised questions through ``POST /query``: none may be a 5xx and at least one must answer.

    A hosted visitor's role can refuse some of them, so a typed error on one is fine. One turn is asked too and
    must end with a typed error or an answer, never a 5xx. The recordings are one-question; the guided turns
    (``check_conversation``) wait for the re-recorded set, behind ``--guided-turns``.
    """
    problems: List[str] = []
    answered, first = 0, None
    try:
        for datasource, question in recorded_questions(meta):
            first = first or (datasource, question)
            result = client.post("/api/v1/query", {"question": question, "datasource_id": datasource})
            answered += result.get("status") == "success"
        if first and not answered:
            problems.append("every advertised question was refused or failed: the keyless demo answered none")
        if first:
            conversation = client.post("/api/v1/conversations", {"datasource_id": first[0]})
            turn = client.post(f"/api/v1/conversations/{conversation['id']}/turns", {"text": first[1]})
            if turn.get("status") != "answered" and not (turn.get("error") or {}).get("code"):
                problems.append(f"a turn ended {turn.get('status')!r} with no typed error code")
    except urllib.error.HTTPError as error:
        problems.append(f"{error.url} answered HTTP {error.code}; a question must end typed, never a server error")
    return problems


def check_answers(client: Client, meta: Mapping, guided_turns: bool = False) -> List[str]:
    problems = check_queries(client, meta)
    if guided_turns:
        problems += check_conversation(client, meta)
    return problems


def check_web_app(base: str, version: str, guided_turns: bool = False) -> List[str]:
    problems = page_problems(*_get(base + "/"))
    client = Client(base)
    problems += health_problems(client.get("/api/v1/health"), version)
    meta = client.get("/api/v1/meta")
    problems += meta_problems(meta)
    problems += index_problems(client.get("/api/v1/index"))
    for datasource in meta.get("datasources") or []:
        problems += schema_problems(client.get(f"/api/v1/datasources/{datasource['id']}/schema"))
    if not problems:
        problems += check_answers(client, meta, guided_turns)
    return problems


def boot_and_check(nl2sql_api: Path, work: Path, version: str, timeout: float, guided_turns: bool = False) -> None:
    port = _free_port()
    base = f"http://{HOST}:{port}"
    log_path = work / "demo.log"
    # Run from the work directory, not the repository, so nothing local is
    # importable by accident.
    with open(log_path, "w", encoding="utf-8") as log:
        proc = subprocess.Popen(demo_command(nl2sql_api, work / "demo", port), cwd=work,
                                env=demo_env(os.environ), stdout=log, stderr=subprocess.STDOUT)
    try:
        wait_until_up(_answers(base + "/api/v1/health"), alive=lambda: proc.poll() is None, timeout=timeout)
        problems = check_web_app(base, version, guided_turns)
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
        raise SystemExit("The web app is up but unhealthy:\n  - " + "\n  - ".join(problems))
    print(f"OK: a fresh install of {version} served the web app, its API, an indexed schema and "
          f"the guided questions at {base}")


def check_live(url: str, version: str, timeout: float, guided_turns: bool = False) -> None:
    """Waits for a deployed web app (the Space) to serve ``version``, then checks it."""
    base = url.rstrip("/")

    def health() -> Optional[Mapping]:
        try:
            return Client(base).get("/api/v1/health")
        except (urllib.error.URLError, OSError, ValueError):
            return None

    wait_for_version(health, version, timeout=timeout)
    problems = check_web_app(base, version, guided_turns)
    if problems:
        raise SystemExit(f"{base} serves {version} but is unhealthy:\n  - " + "\n  - ".join(problems))
    print(f"OK: {base} serves {version}, its page, its API and the guided questions")


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--dist", type=Path, default=REPO / "dist",
                        help="install the wheels in this folder (default: dist/)")
    source.add_argument("--pypi", metavar="VERSION",
                        help="install this published version from PyPI instead, retrying while the index catches up")
    source.add_argument("--url", help="check a deployed web app instead of installing one; needs --expect-version")
    parser.add_argument("--build", action="store_true", help="build the three distributions into --dist first")
    parser.add_argument("--expect-version", help="with --url: the version /api/v1/health must report")
    parser.add_argument("--attempts", type=int, default=10, help="with --pypi: install attempts (default 10, 30s apart)")
    parser.add_argument("--timeout", type=float, default=900,
                        help="seconds to wait for the web app to serve (default 900; the first run downloads a model)")
    parser.add_argument("--guided-turns", action="store_true",
                        help="also ask every guided question and the follow-up as conversation turns (R1 turns this on "
                             "once the answers are re-recorded)")
    parser.add_argument("--keep", action="store_true", help="keep the virtualenv and demo folder afterwards")
    args = parser.parse_args(argv)

    if args.url:
        if not args.expect_version:
            parser.error("--url needs --expect-version")
        check_live(args.url, args.expect_version, args.timeout, args.guided_turns)
        return

    if args.pypi:
        version, install_args, attempts = args.pypi, pypi_install_args(args.pypi), args.attempts
    else:
        dist = args.dist.resolve()
        if args.build:
            build(dist)
        wheels = find_wheels(dist)
        version, install_args, attempts = wheel_version(wheels["engine"]), wheel_install_args(wheels, dist), 1

    work = Path(tempfile.mkdtemp(prefix="nl2sql-fresh-install-"))
    try:
        nl2sql_api = install(work / "venv", install_args, attempts=attempts)
        boot_and_check(nl2sql_api, work, version, args.timeout, args.guided_turns)
    finally:
        if args.keep:
            print(f"Kept {work}")
        else:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
