"""`nl2sql demo`: scaffold a demo project, index it, and serve the playground.

Three modes, chosen at process start from the environment only:

``replay``  no key anywhere, so the guided questions answer from recordings
``live``    a key (or a reachable Ollama) is present, so questions go upstream
``record``  ``--record`` with a key, capturing upstream answers for replay

The playground never stores a key and never writes to the demo database.
"""
from __future__ import annotations

import os
import pathlib
import urllib.request
import webbrowser
from typing import List, Optional, Tuple

import yaml

from nl2sql.cli.common.decorators import handle_cli_errors
from nl2sql.cli.console import console, print_error, print_step, print_success
from nl2sql.cli.demo import DemoManager
from nl2sql.cli.demo.chinook import CHINOOK_QUESTIONS
from nl2sql.common.settings import reload_settings
from nl2sql.llm.replay import RecordingProxy, ReplayStore
from nl2sql.testing.fake_llm import FakeLLMServer

RECORDINGS = pathlib.Path(__file__).resolve().parent.parent / "demo" / "recordings"

INSTALL_HINT = 'Install the demo extra: pip install "nl2sql-engine[demo]"'

# `.env.demo` ships an empty `OPENAI_API_KEY=` placeholder and indexing loads it
# with `override=True`, which blanks a real key already in the environment. Live
# mode then silently fell back to `ollama` with no key at all, so the keys the
# mode was chosen from are restored once scaffolding is done.
PROVIDER_KEYS = ("OPENAI_API_KEY", "OPENROUTER_API_KEY")


def _ollama_reachable() -> bool:
    try:
        with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=1.0) as response:
            return response.status == 200
    except Exception:
        return False


def detect_llm_mode() -> str:
    if os.environ.get("OPENAI_API_KEY") or os.environ.get("OPENROUTER_API_KEY") or _ollama_reachable():
        return "live"
    return "replay"


def _upstream_from_env() -> Tuple[str, str, str]:
    key = os.environ.get("OPENAI_API_KEY")
    if key:
        return "https://api.openai.com/v1", key, "openai"
    return "https://openrouter.ai/api/v1", os.environ.get("OPENROUTER_API_KEY", ""), "openrouter"


def prepare_project(directory: pathlib.Path, dataset: str) -> pathlib.Path:
    directory.mkdir(parents=True, exist_ok=True)
    manager = DemoManager(console, directory)
    if not (directory / "configs" / "datasources.demo.yaml").exists():
        print_step(f"Writing the {dataset} demo project to {directory}")
        if dataset == "chinook":
            manager.setup_chinook()
        else:
            manager.setup_lite()
    if not (directory / "data" / "vector_store_demo").exists():
        print_step("Indexing the schema (first run downloads a 79 MB embedding model)")
        manager.index_demo_data()
    return directory


def _point_llm_config_at(directory: pathlib.Path, base_url: Optional[str], provider: str = "openai") -> None:
    path = directory / "configs" / "llm.demo.yaml"
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    cfg["default"]["provider"] = provider
    if base_url:
        cfg["default"]["base_url"] = base_url
    else:
        cfg["default"].pop("base_url", None)
    path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")


def _serve(app, host: str, port: int) -> None:
    import uvicorn

    uvicorn.run(app, host=host, port=port, log_level="warning")


def _build_engine():
    """Seam: the tests swap this so they never construct a real engine."""
    from nl2sql import NL2SQL

    return NL2SQL()


def _manufacturing_questions(directory: pathlib.Path) -> List[str]:
    path = directory / "configs" / "sample_questions.demo.yaml"
    if not path.exists():
        return []
    grouped = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [q for questions in grouped.values() for q in questions]


def _record_all(engine, questions: List[str], proxy, store: ReplayStore, store_path: pathlib.Path) -> None:
    """Drives every guided question through the proxy so replay has an answer."""
    from nl2sql.auth.models import UserContext

    for question in questions:
        result = engine.run_query(question, execute=True, user_context=UserContext(roles=["admin"]))
        console.print(f"  [{result.status or 'unknown'}] {question}")

    if questions:
        # The denial path still calls the planner, so the last question has to be
        # asked as viewer too or replay has no recording for the rejected plan.
        denied = engine.run_query(
            questions[-1], execute=True, user_context=UserContext(roles=["viewer"])
        )
        console.print(f"  [{denied.status or 'unknown'}] (as viewer) {questions[-1]}")

    proxy.stop()
    store.save(store_path)
    print_success(f"Recorded {len(store.rules())} responses to {store_path}")


@handle_cli_errors
def demo_command(
    dataset: str,
    directory: pathlib.Path,
    host: str,
    port: int,
    no_browser: bool,
    record: bool,
) -> None:
    mode = detect_llm_mode()
    # A reachable Ollama makes the mode "live" but gives recording nothing to
    # proxy through, so --record asks for a key directly rather than for a mode.
    if record and not any(os.environ.get(key) for key in PROVIDER_KEYS):
        print_error("--record needs OPENAI_API_KEY or OPENROUTER_API_KEY set.")
        raise SystemExit(1)

    # chdir before anything indexes. The schema store path is resolved against
    # the working directory rather than the project root, so indexing from a
    # different cwd than the one the engine later runs in writes the snapshot
    # where the playground cannot find it and /api/schema comes back empty.
    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    os.chdir(directory)
    os.environ["ENV"] = "demo"

    preserved = {key: os.environ[key] for key in PROVIDER_KEYS if os.environ.get(key)}
    directory = prepare_project(directory, dataset)
    for key, value in preserved.items():
        if not os.environ.get(key):
            os.environ[key] = value

    replay_server = None
    proxy = None
    store: Optional[ReplayStore] = None
    store_path = directory / "recordings.json"

    if record:
        upstream, key, _provider = _upstream_from_env()
        store = ReplayStore.load(store_path) if store_path.exists() else ReplayStore()
        proxy = RecordingProxy(upstream, key, store).start()
        _point_llm_config_at(directory, proxy.base_url, provider="openai")
        os.environ["OPENAI_API_KEY"] = os.environ.get("OPENAI_API_KEY") or "proxy"
        console.print("[bold]Recording mode:[/bold] running the sample questions through the real provider.")
    elif mode == "replay":
        recordings = RECORDINGS / f"{dataset}.json"
        store = ReplayStore.load(recordings) if recordings.exists() else ReplayStore()
        replay_server = FakeLLMServer(store.rules()).start()
        _point_llm_config_at(directory, replay_server.base_url)
        os.environ["OPENAI_API_KEY"] = "replay"
        console.print(
            "[bold]Replay mode:[/bold] no API key found, the guided questions use recorded responses. "
            "Set OPENAI_API_KEY (or OPENROUTER_API_KEY, or run Ollama) for live mode."
        )
    else:
        if os.environ.get("OPENAI_API_KEY"):
            provider = "openai"
        elif os.environ.get("OPENROUTER_API_KEY"):
            provider = "openrouter"
        else:
            provider = "ollama"
        _point_llm_config_at(directory, None, provider=provider)
        console.print(f"[bold]Live mode:[/bold] using {provider}.")

    reload_settings()

    try:
        from nl2sql.cli.demo.playground.app import build_app
    except ImportError:
        print_error(INSTALL_HINT)
        raise SystemExit(1)

    engine = _build_engine()
    questions = CHINOOK_QUESTIONS if dataset == "chinook" else _manufacturing_questions(directory)
    roles = list(engine.context.policies_cfg.roles)

    if record:
        _record_all(engine, questions, proxy, store, store_path)
        return

    app = build_app(
        engine,
        questions=questions,
        roles=roles,
        mode="replay" if replay_server else "live",
        dataset=dataset,
    )
    url = f"http://{host}:{port}/"
    print_success(f"Playground ready at {url}")
    if not no_browser:
        webbrowser.open(url)
    try:
        _serve(app, host, port)
    finally:
        if replay_server:
            replay_server.stop()
