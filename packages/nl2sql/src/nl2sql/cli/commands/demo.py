"""`nl2sql demo`: scaffold a demo project, index it, and serve the playground.

Three modes, chosen at process start from the first key that turns up:

``replay``  no key anywhere, so questions answer only from recordings: the
            demo project's ``recordings.json`` (written by ``--record``), else
            any packaged with the engine (none ship today)
``live``    a key (or a reachable Ollama) is present, so questions go upstream
``record``  ``--record`` with a key, capturing upstream answers for replay

The key is looked for in a fixed order, highest precedence first:

1. ``--api-key`` on the command line, which is also written into the demo
   project's ``.env.demo`` so later runs from that directory stay live
2. ``OPENAI_API_KEY`` / ``OPENROUTER_API_KEY`` / ``ANTHROPIC_API_KEY`` in the
   process environment
3. whichever of those is already recorded in the demo project's ``.env.demo``
4. a reachable Ollama (live, but nothing to record through)
5. replay

``--api-key`` and the playground's settings panel are the only sources that
write a key down, and both write only to ``.env.demo``, through
:func:`_persist_api_key`. A key saved in the panel takes effect at once, and on
the next start it sits at step 3 of the order above. No path echoes a key: the
console and the playground show at most a masked form. The panel is on only
for a loopback ``--host`` unless ``--allow-settings`` is given; the playground
never writes to the demo database.
"""
from __future__ import annotations

import os
import pathlib
import urllib.request
import webbrowser
from typing import List, Optional, Tuple

import yaml

from nl2sql.cli.common.api_key import (
    default_model_for,
    default_temperature_for,
    env_var_for_key,
    env_var_for_provider,
    mask_key,
    provider_for_key,
)
from nl2sql.cli.common.decorators import handle_cli_errors
from nl2sql.cli.console import console, print_error, print_step, print_success
from nl2sql.cli.demo import DemoManager
from nl2sql.cli.demo.chinook import CHINOOK_QUESTIONS
from nl2sql.cli.demo.stamp import outdated_warning
from rich.markup import escape
from nl2sql.common.settings import reload_settings
from nl2sql.llm.replay import RecordingProxy, ReplayStore
from nl2sql.testing.fake_llm import FakeLLMServer

RECORDINGS = pathlib.Path(__file__).resolve().parent.parent / "demo" / "recordings"

INSTALL_HINT = 'Install the demo extra: pip install "nl2sql-engine[demo]"'

# The only demo dataset. Kept as a name rather than inlined because the
# playground, the replay recordings and the scaffolding all key off it.
DATASET = "chinook"

# `.env.demo` ships an empty `OPENAI_API_KEY=` placeholder. Indexing used to load
# it with `override=True`, which blanked a real key already in the environment,
# so live mode fell back to `ollama` and enrichment ran with no key. Indexing now
# keeps a key already set (`manager._load_demo_env`); restoring the keys the mode
# was chosen from after scaffolding stays as a second guard.
PROVIDER_KEYS = ("OPENAI_API_KEY", "OPENROUTER_API_KEY", "ANTHROPIC_API_KEY")


def _ollama_reachable() -> bool:
    try:
        with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=1.0) as response:
            return response.status == 200
    except Exception:
        return False


def detect_llm_mode() -> str:
    if any(os.environ.get(name) for name in PROVIDER_KEYS) or _ollama_reachable():
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
        for provider in ("openai", "openrouter", "anthropic"):
            if os.environ.get(env_var_for_provider(provider)) == key:
                return provider
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


_INDEX_STATE = {
    "missing": "No vector index yet",
    "empty": "The vector index is empty",
    "stale": "The vector index is out of date",
}


def prepare_project(directory: pathlib.Path) -> pathlib.Path:
    """Scaffolds the demo folder if needed and makes sure its index is usable.

    The index is judged by its contents (entries, and whether they match the
    latest schema snapshot), not by whether its folder exists: a folder with
    an empty collection once passed the old check on every start.
    """
    directory.mkdir(parents=True, exist_ok=True)
    manager = DemoManager(console, directory)
    if not (directory / "configs" / "datasources.demo.yaml").exists():
        print_step(f"Writing the {DATASET} demo project to {directory}")
        manager.setup_chinook()
    else:
        warning = outdated_warning(directory)
        if warning:
            console.print(f"[warning]{escape(warning)}[/warning]")

    health = manager.index_health()
    if not health.ok:
        print_step(
            f"{_INDEX_STATE.get(health.status, 'The vector index needs rebuilding')}: indexing the schema "
            "(first run downloads a 79 MB embedding model)"
        )
        if not manager.index_demo_data():
            print_error(
                "Indexing failed, so questions will fail until the index is rebuilt. "
                "Any previous index is unchanged. Fix the error above, then press Rebuild "
                "in the playground or run: nl2sql --env demo index"
            )
    return directory


def _point_llm_config_at(directory: pathlib.Path, base_url: Optional[str], provider: str = "openai") -> None:
    """Points the default agent, and every per-node agent, at one endpoint.

    A per-node entry (written by the playground's settings panel) differs from
    the default only in its model, so it follows the default's provider and
    endpoint: in replay mode a node left on the real provider would be sent the
    ``replay`` placeholder as its key.

    The key reference follows the provider too. The scaffolded config reads
    ``${env:OPENAI_API_KEY}``, and a reference names the one variable the
    registry looks in, so an OpenRouter demo kept failing with "no API key"
    while ``OPENROUTER_API_KEY`` was set.

    Moving between OpenAI and Anthropic also moves the model: a ``gpt-`` model
    means nothing to Anthropic, nor a ``claude-`` one to OpenAI. The model is
    replaced by the provider's default, with the temperature that model takes.
    """
    path = directory / "configs" / "llm.demo.yaml"
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    key_variable = env_var_for_provider(provider) if provider in ("openai", "openrouter", "anthropic") else None
    for agent in [cfg["default"], *(cfg.get("agents") or {}).values()]:
        agent["provider"] = provider
        if provider in ("openai", "anthropic") and (
            (provider == "anthropic") != str(agent.get("model", "")).startswith("claude-")
        ):
            agent["model"] = default_model_for(provider)
            agent["temperature"] = default_temperature_for(provider)
        if key_variable:
            agent["api_key"] = "${env:" + key_variable + "}"
        if base_url:
            agent["base_url"] = base_url
        else:
            agent.pop("base_url", None)
    path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")


def replay_recordings(directory: pathlib.Path) -> Optional[pathlib.Path]:
    """The recordings replay mode reads, or None when there are none.

    The demo project's own ``recordings.json`` (what ``--record`` writes) wins;
    otherwise the recordings packaged with the engine, if any ship.
    """
    for path in (directory / "recordings.json", RECORDINGS / f"{DATASET}.json"):
        if path.exists():
            return path
    return None


def replay_message(recorded: int, total: int, recordings: Optional[pathlib.Path]) -> str:
    """The console line for replay mode; it claims recorded answers only when there are some."""
    ask = (
        "add an API key in the playground's Settings panel or pass --api-key "
        "(or set OPENAI_API_KEY, OPENROUTER_API_KEY or ANTHROPIC_API_KEY, or run Ollama)."
    )
    if not recorded:
        return (
            "[bold]Replay mode:[/bold] no API key found, and replay mode has no recorded answers, "
            f"so no question can be answered. To ask questions, {ask}"
        )
    return (
        f"[bold]Replay mode:[/bold] no API key found. {recorded} of {total} guided questions answer "
        f"from recorded responses in {escape(str(recordings))}. For any other question, {ask}"
    )


def _serve(app, host: str, port: int) -> None:
    import uvicorn

    uvicorn.run(app, host=host, port=port, log_level="warning")


def _build_engine():
    """Seam: the tests swap this so they never construct a real engine."""
    from nl2sql import NL2SQL

    return NL2SQL()


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
    directory: pathlib.Path,
    host: str,
    port: int,
    no_browser: bool,
    record: bool,
    api_key: Optional[str] = None,
    allow_settings: bool = False,
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
    # The recording proxy and replay speak the OpenAI wire format; Claude does not.
    if record and live_provider(resolved_key, key_source) not in UPSTREAMS:
        print_error("--record needs an OpenAI or OpenRouter key: recordings capture the OpenAI wire format.")
        raise SystemExit(1)

    preserved = {key: os.environ[key] for key in PROVIDER_KEYS if os.environ.get(key)}
    directory = prepare_project(directory)
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
    recorded_questions = 0
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
        # A plan served from the plan cache makes no planner call, so nothing
        # would be recorded for replay to answer with.
        os.environ["PLAN_CACHE_ENABLED"] = "false"
        console.print("[bold]Recording mode:[/bold] running the sample questions through the real provider.")
    elif mode == "replay":
        recordings = replay_recordings(directory)
        store = ReplayStore.load(recordings) if recordings else ReplayStore()
        recorded_questions = len(store.covered(CHINOOK_QUESTIONS))
        replay_server = FakeLLMServer(store.rules()).start()
        _point_llm_config_at(directory, replay_server.base_url)
        os.environ["OPENAI_API_KEY"] = "replay"
        console.print(replay_message(recorded_questions, len(CHINOOK_QUESTIONS), recordings))
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
    questions = list(CHINOOK_QUESTIONS)
    roles = list(engine.context.policies_cfg.roles)

    if record:
        _record_all(engine, questions, proxy, store, store_path)
        return

    app = build_app(
        engine,
        questions=questions,
        roles=roles,
        mode="replay" if replay_server else "live",
        dataset=DATASET,
        project_dir=directory,
        host=host,
        allow_settings=allow_settings,
        recorded_questions=recorded_questions,
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
