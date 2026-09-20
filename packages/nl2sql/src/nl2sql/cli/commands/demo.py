"""`nl2sql demo`: scaffold a demo project, index it, and serve the playground.

Three modes, chosen at process start from the first key that turns up:

``replay``  no key anywhere, so the guided questions answer from recordings
``live``    a key (or a reachable Ollama) is present, so questions go upstream
``record``  ``--record`` with a key, capturing upstream answers for replay

The key is looked for in a fixed order, highest precedence first:

1. ``--api-key`` on the command line, which is also written into the demo
   project's ``.env.demo`` so later runs from that directory stay live
2. ``OPENAI_API_KEY`` / ``OPENROUTER_API_KEY`` in the process environment
3. whichever of those is already recorded in the demo project's ``.env.demo``
4. a reachable Ollama (live, but nothing to record through)
5. replay

``--api-key`` is the only source that writes anything down, and it writes only
to ``.env.demo``. No path echoes a key to the console: what is printed is at
most a masked form. The playground itself never sees a key and never writes to
the demo database.
"""
from __future__ import annotations

import os
import pathlib
import urllib.request
import webbrowser
from typing import List, Optional, Tuple

import yaml

from nl2sql.cli.common.api_key import env_var_for_key, mask_key, provider_for_key
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


UPSTREAMS = {
    "openai": "https://api.openai.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
}


def live_provider(key: Optional[str], source: str) -> str:
    """Names the provider live mode will call with ``key``.

    A key the user exported themselves is trusted to be in the right variable
    whatever it looks like; one from ``--api-key`` or from ``.env.demo`` is
    placed, and so read back, by its own shape.
    """
    if not key:
        return "ollama"
    if source == "environment":
        return "openai" if os.environ.get("OPENAI_API_KEY") == key else "openrouter"
    return provider_for_key(key)


def _key_from_env_file(path: pathlib.Path) -> Optional[str]:
    """Returns a non-empty provider key recorded in ``path``, if there is one.

    The file is read, not loaded: `.env.demo` ships an empty ``OPENAI_API_KEY=``
    placeholder, and putting that into the environment is exactly the bug that
    blanked a real key.
    """
    if not path.exists():
        return None

    from dotenv import dotenv_values

    values = dotenv_values(path)
    for name in PROVIDER_KEYS:
        value = (values.get(name) or "").strip()
        if value:
            return value
    return None


def resolve_api_key(api_key: Optional[str], env_file: pathlib.Path) -> Tuple[Optional[str], str]:
    """Finds the key live mode should use and says where it came from.

    Args:
        api_key: the value of ``--api-key``, if it was passed.
        env_file: the demo project's ``.env.demo``, which may not exist yet.

    Returns:
        ``(key, source)`` where source is ``"flag"``, ``"environment"``,
        ``"env-file"`` or ``"none"``. Ollama and replay are decided after this,
        by :func:`detect_llm_mode`, so they are not sources of a key.
    """
    if api_key and api_key.strip():
        return api_key.strip(), "flag"
    for name in PROVIDER_KEYS:
        value = os.environ.get(name)
        if value:
            return value, "environment"
    from_file = _key_from_env_file(env_file)
    if from_file:
        return from_file, "env-file"
    return None, "none"


def _persist_api_key(path: pathlib.Path, key: str) -> None:
    """Records the key in the demo project's ``.env.demo``.

    Both provider assignments are replaced by the single one this key needs.
    Leaving the shipped empty ``OPENAI_API_KEY=`` placeholder below a real
    value would blank it again when indexing loads the file with
    ``override=True``, and leaving a stale key for the other provider would win
    the precedence order on the next run.
    """
    variable = env_var_for_key(key)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []

    kept: List[str] = []
    written = False
    for line in lines:
        if line.split("=", 1)[0].strip() in PROVIDER_KEYS:
            if not written:
                kept.append(f"{variable}={key}")
                written = True
            continue
        kept.append(line)
    if not written:
        kept.append(f"{variable}={key}")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(kept) + "\n", encoding="utf-8")


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
    api_key: Optional[str] = None,
) -> None:
    # chdir before anything indexes. The schema store path is resolved against
    # the working directory rather than the project root, so indexing from a
    # different cwd than the one the engine later runs in writes the snapshot
    # where the playground cannot find it and /api/schema comes back empty.
    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    os.chdir(directory)
    os.environ["ENV"] = "demo"

    env_file = directory / ".env.demo"
    resolved_key, key_source = resolve_api_key(api_key, env_file)
    if resolved_key and key_source != "environment":
        # A key already exported by the user stays under the variable they
        # chose; one from the flag or from the file is placed by its own shape.
        os.environ[env_var_for_key(resolved_key)] = resolved_key

    mode = detect_llm_mode()
    # A reachable Ollama makes the mode "live" but gives recording nothing to
    # proxy through, so --record asks for a key directly rather than for a mode.
    if record and not resolved_key:
        print_error("--record needs an API key.")
        console.print("Pass --api-key, or set OPENAI_API_KEY or OPENROUTER_API_KEY.")
        raise SystemExit(1)

    preserved = {key: os.environ[key] for key in PROVIDER_KEYS if os.environ.get(key)}
    directory = prepare_project(directory, dataset)
    for key, value in preserved.items():
        if not os.environ.get(key):
            os.environ[key] = value

    if key_source == "flag":
        # Scaffolding writes `.env.demo` (with an empty placeholder), so the
        # key goes in afterwards whether the project is new or already there.
        _persist_api_key(env_file, resolved_key)
        print_success(
            f"Saved {env_var_for_key(resolved_key)} ({mask_key(resolved_key)}) to "
            f"{env_file.name}; later runs from this directory are live."
        )

    replay_server = None
    proxy = None
    store: Optional[ReplayStore] = None
    store_path = directory / "recordings.json"

    if record:
        # --record already refused to start without a key, so the provider here
        # is never "ollama".
        upstream = UPSTREAMS[live_provider(resolved_key, key_source)]
        store = ReplayStore.load(store_path) if store_path.exists() else ReplayStore()
        proxy = RecordingProxy(upstream, resolved_key, store).start()
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
            "Pass --api-key, set OPENAI_API_KEY or OPENROUTER_API_KEY, or run Ollama for live mode."
        )
    else:
        provider = live_provider(resolved_key, key_source)
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
