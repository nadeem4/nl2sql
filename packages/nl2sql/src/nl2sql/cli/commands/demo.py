"""`nl2sql demo`: scaffold a demo project, index it, and serve the playground.

``--hosted`` (or ``NL2SQL_DEMO_HOSTED=1``) is a fourth mode and a different
server: it is for a public demo, where the process holds no key at all and
every visitor brings their own in a request header; a visitor without one is
answered from the packaged recordings for the guided questions they cover. Nothing is saved -- not the
key, not settings, not the index, not feedback -- and per-visitor limits apply.
It is not a loosened ``--allow-settings``: settings, Rebuild, feedback and
``--record`` are all refused there. See
:mod:`nl2sql.cli.demo.playground.hosted`.

Otherwise three modes, chosen at process start from the first key that turns up:

``replay``  no key anywhere, so questions answer only from recordings: the
            demo project's ``recordings.json`` (written by ``--record``), else
            any packaged with the engine (``scripts/record_demo_answers.py``
            makes those)
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
:func:`nl2sql.cli.demo.llm_config.persist_api_key`. A key saved in the panel takes effect at once, and on
the next start it sits at step 3 of the order above. No path echoes a key: the
console and the playground show at most a masked form. The panel (and, by the
same gate, Rebuild and the Retrieval inspector) is on only for a loopback
``--host`` unless ``--allow-settings`` is given; the playground never writes to
the demo database.
"""
from __future__ import annotations

import os
import pathlib
import urllib.request
import webbrowser
from typing import List, Optional, Tuple

import yaml

from nl2sql.llm.providers import (
    ANTHROPIC_UPSTREAM,
    KEYED_PROVIDERS,
    PROVIDER_KEYS,
    UPSTREAMS,
    env_var_for_key,
    env_var_for_provider,
    mask_key,
    provider_for_key,
)
from nl2sql.cli.common.decorators import handle_cli_errors
from nl2sql.cli.console import console, print_error, print_step, print_success
from nl2sql.cli.demo import DemoManager
from nl2sql.cli.demo.datasets import DEMO_QUESTIONS, DEMO_QUESTIONS_BY_DATASOURCE
from nl2sql.cli.demo.llm_config import persist_api_key as _persist_api_key
from nl2sql.cli.demo.llm_config import point_llm_config_at as _point_llm_config_at
from nl2sql.cli.demo.stamp import outdated_warning
from rich.markup import escape
from nl2sql.common.settings import reload_settings
from nl2sql.llm.registry import PROVIDER_PRESETS
from nl2sql.llm.replay import RecordingProxy, ReplayStore
from nl2sql.testing.fake_llm import FakeLLMServer

RECORDINGS = pathlib.Path(__file__).resolve().parent.parent / "demo" / "recordings"

INSTALL_HINT = 'Install the demo extra: pip install "nl2sql-engine[demo]"'

# The demo's lead dataset. Three databases are registered (see
# `nl2sql.cli.demo.datasets`); this one names the packaged recording file and
# the datasource the playground's schema panel opens on.
DATASET = "chinook"

# `.env.demo` ships an empty `OPENAI_API_KEY=` placeholder. Indexing used to load
# it with `override=True`, which blanked a real key already in the environment,
# so live mode fell back to `ollama` and enrichment ran with no key. Indexing now
# keeps a key already set (`manager._load_demo_env`); restoring the keys the mode
# was chosen from after scaffolding stays as a second guard. Those keys are
# ``PROVIDER_KEYS``; it, ``UPSTREAMS`` (where the recording proxy forwards each
# OpenAI-wire provider) and ``KEYED_PROVIDERS`` (the providers a saved key can
# select) all come from the provider presets in ``nl2sql.llm``.

# Ollama's own API sits next to the OpenAI-compatible /v1 its preset names.
OLLAMA_TAGS_URL = PROVIDER_PRESETS["ollama"].base_url.rsplit("/v1", 1)[0] + "/api/tags"


def _ollama_reachable() -> bool:
    try:
        with urllib.request.urlopen(OLLAMA_TAGS_URL, timeout=1.0) as response:
            return response.status == 200
    except Exception:
        return False


def detect_llm_mode() -> str:
    if any(os.environ.get(name) for name in PROVIDER_KEYS) or _ollama_reachable():
        return "live"
    return "replay"


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


_INDEX_STATE = {
    "missing": "No vector index yet",
    "empty": "The vector index is empty",
    "stale": "The vector index is out of date",
}


def prepare_project(directory: pathlib.Path, require_index: bool = False) -> pathlib.Path:
    """Scaffolds the demo folder if needed and makes sure its index is usable.

    The index is judged by its contents (entries, and whether they match the
    latest schema snapshot), not by whether its folder exists: a folder with
    an empty collection once passed the old check on every start.

    A failed index is not fatal by default -- the playground shows it and
    offers Rebuild. ``require_index`` (``--record``) makes it exit 1 instead:
    answers recorded against no index are the wrong answers.
    """
    directory.mkdir(parents=True, exist_ok=True)
    manager = DemoManager(console, directory)
    if not (directory / "configs" / "datasources.demo.yaml").exists():
        print_step(f"Writing the demo project to {directory}")
        manager.setup_demo()
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
            if require_index:
                print_error(
                    "Indexing failed, so --record stops here: answers recorded against no index "
                    "would be wrong. Nothing was recorded. Fix the error above and run it again."
                )
                raise SystemExit(1)
            print_error(
                "Indexing failed, so questions will fail until the index is rebuilt. "
                "Any previous index is unchanged. Fix the error above, then press Rebuild "
                "in the playground or run: nl2sql --env demo index"
            )
    return directory


def _load_saved_keys(path: pathlib.Path) -> None:
    """Puts every provider key saved in ``.env.demo`` into the environment.

    A node the settings panel put on another provider reads that provider's
    key. A key already exported wins, and the shipped empty placeholders are
    skipped, so nothing is blanked.
    """
    if not path.exists():
        return
    from dotenv import dotenv_values

    for name, value in dotenv_values(path).items():
        if name in PROVIDER_KEYS and (value or "").strip() and not os.environ.get(name):
            os.environ[name] = value.strip()


def _default_provider(directory: pathlib.Path) -> Optional[str]:
    path = directory / "configs" / "llm.demo.yaml"
    if not path.exists():
        return None
    return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("default", {}).get("provider")


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


def run_succeeded(result) -> bool:
    """Whether a run answered: rows, an answer, and no error.

    Only such a run is worth replaying. Every model call is recorded as it
    passes through the proxy, so a run that failed later -- in the
    aggregator, say -- still leaves recordings behind.
    """
    return (getattr(result, "status", "") == "success"
            and bool(getattr(result, "final_answer", None))
            and not getattr(result, "errors", None))


def _record_all(engine, questions: List[str], proxy, store: ReplayStore, store_path: pathlib.Path) -> List[str]:
    """Drives every guided question through the proxy so replay has an answer.

    Returns the questions whose run did not succeed (:func:`run_succeeded`).
    Their recordings are dropped before the file is written, so replay never
    serves a half-run and the question counts as not recorded.
    """
    from nl2sql.auth.models import UserContext

    failed: List[str] = []
    for question in questions:
        result = engine.run_query(question, execute=True, user_context=UserContext(roles=["admin"]))
        console.print(escape(f"  [{result.status or 'unknown'}] {question}"))
        if not run_succeeded(result):
            failed.append(question)
            for error in getattr(result, "errors", None) or []:
                message = error.get("message") if isinstance(error, dict) else str(error)
                console.print(f"      {escape(str(message))}")

    if questions:
        # The denial path still calls the planner, so the last question has to be
        # asked as viewer too or replay has no recording for the rejected plan.
        # It is meant to be refused, so its status is not a failure.
        denied = engine.run_query(
            questions[-1], execute=True, user_context=UserContext(roles=["viewer"])
        )
        console.print(escape(f"  [{denied.status or 'unknown'}] (as viewer) {questions[-1]}"))

    proxy.stop()
    for question in failed:
        store.discard(question)
    store.save(store_path)
    print_success(f"Recorded {len(questions) - len(failed)} of {len(questions)} guided questions "
                  f"({len(store.rules())} responses) to {store_path}")
    if failed:
        print_error(f"{len(failed)} runs failed and were not recorded:")
        for question in failed:
            console.print(f"  not recorded: {escape(question)}")
    return failed


@handle_cli_errors
def demo_command(
    directory: pathlib.Path,
    host: str,
    port: int,
    no_browser: bool,
    record: bool,
    api_key: Optional[str] = None,
    allow_settings: bool = False,
    hosted: bool = False,
) -> None:
    # The playground -- and hosted mode with it -- comes from the demo extra.
    # Asked for here so a missing extra is one clear line, not an ImportError
    # halfway through scaffolding.
    try:
        from nl2sql.cli.demo.playground.hosted import Replay
        from nl2sql.cli.demo.playground.hosted import from_env as _hosted_from_env
    except ImportError:
        print_error(INSTALL_HINT)
        raise SystemExit(1)

    hosted_mode = _hosted_from_env(hosted)
    if hosted_mode.enabled and record:
        print_error("--record is refused in hosted mode: it would write recordings to the server's disk.")
        raise SystemExit(1)
    if hosted_mode.enabled and api_key:
        print_error("--api-key is refused in hosted mode: the server holds no key; each visitor brings one.")
        raise SystemExit(1)

    # chdir before anything indexes. The schema store path is resolved against
    # the working directory rather than the project root, so indexing from a
    # different cwd than the one the engine later runs in writes the snapshot
    # where the playground cannot find it and /api/schema comes back empty.
    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    os.chdir(directory)
    os.environ["ENV"] = "demo"

    env_file = directory / ".env.demo"
    if hosted_mode.enabled:
        # No key is looked for and none is used: a key found here would be the
        # owner's, and a public demo must never spend it.
        resolved_key, key_source = None, "none"
    else:
        resolved_key, key_source = resolve_api_key(api_key, env_file)
    if resolved_key and key_source != "environment":
        # A key already exported by the user stays under the variable they
        # chose; one from the flag or from the file is placed by its own shape.
        os.environ[env_var_for_key(resolved_key)] = resolved_key

    mode = "hosted" if hosted_mode.enabled else detect_llm_mode()
    # A reachable Ollama makes the mode "live" but gives recording nothing to
    # proxy through, so --record asks for a key directly rather than for a mode.
    if record and not resolved_key:
        print_error("--record needs an API key.")
        console.print("Pass --api-key, or set OPENAI_API_KEY, OPENROUTER_API_KEY or ANTHROPIC_API_KEY.")
        raise SystemExit(1)
    # The recording proxy speaks the OpenAI wire and Anthropic's Messages API;
    # replay answers both from the same recordings.
    record_provider = live_provider(resolved_key, key_source) if record else None
    if record and record_provider not in UPSTREAMS and record_provider != "anthropic":
        print_error("--record needs an OpenAI, OpenRouter or Anthropic key.")
        raise SystemExit(1)

    preserved = {key: os.environ[key] for key in PROVIDER_KEYS if os.environ.get(key)}
    directory = prepare_project(directory, require_index=record)
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
        store = ReplayStore.load(store_path) if store_path.exists() else ReplayStore()
        if record_provider == "anthropic":
            proxy = RecordingProxy(ANTHROPIC_UPSTREAM, resolved_key, store, wire="anthropic").start()
            _point_llm_config_at(directory, proxy.anthropic_base_url, provider="anthropic")
            anthropic_env = env_var_for_provider("anthropic")
            os.environ[anthropic_env] = os.environ.get(anthropic_env) or "proxy"
        else:
            proxy = RecordingProxy(UPSTREAMS[record_provider], resolved_key, store).start()
            _point_llm_config_at(directory, proxy.base_url, provider="openai")
            os.environ["OPENAI_API_KEY"] = os.environ.get("OPENAI_API_KEY") or "proxy"
        # A plan served from the plan cache makes no planner call, so nothing
        # would be recorded for replay to answer with.
        os.environ["PLAN_CACHE_ENABLED"] = "false"
        console.print("[bold]Recording mode:[/bold] running the sample questions through the real provider.")
    elif mode == "hosted":
        # The config is normalised once, here, and never written again: a
        # visitor's key moves the client to their own provider per request
        # (``LLMRegistry._for_key``) and is stored nowhere.
        _point_llm_config_at(directory, None, provider="openai")
        # Whatever the host exported, the server runs with no key of its own,
        # so a bug cannot quietly fall back to the owner's.
        for name in PROVIDER_KEYS:
            os.environ.pop(name, None)
        # A visitor with no key is served the packaged recordings for the
        # guided questions they cover, from a replay server on loopback that
        # only this process can reach. Every other keyless question is a
        # replay miss that asks for a key.
        recordings = replay_recordings(directory)
        store = ReplayStore.load(recordings) if recordings else ReplayStore()
        covered = store.covered(DEMO_QUESTIONS)
        recorded_questions = len(covered)
        if covered:
            replay_server = FakeLLMServer(store.rules()).start()
            hosted_mode.replay = Replay(replay_server.base_url, covered)
        console.print(
            "[bold]Hosted mode:[/bold] the server holds no API key. Each visitor pastes their own "
            "under Settings; it stays in their browser tab, travels with each question and is "
            "never written down here. Settings, Rebuild, feedback and --record are off, and "
            f"questions are limited to {hosted_mode.limits.per_minute} a minute and "
            f"{hosted_mode.limits.per_session} a session."
        )
        console.print(
            f"{recorded_questions} guided questions answer from recordings without a key."
            if recorded_questions else
            "No recordings ship with this install, so a visitor needs a key for every question."
        )
    elif mode == "replay":
        recordings = replay_recordings(directory)
        store = ReplayStore.load(recordings) if recordings else ReplayStore()
        recorded_questions = len(store.covered(DEMO_QUESTIONS))
        replay_server = FakeLLMServer(store.rules()).start()
        _point_llm_config_at(directory, replay_server.base_url)
        os.environ["OPENAI_API_KEY"] = "replay"
        console.print(replay_message(recorded_questions, len(DEMO_QUESTIONS), recordings))
    else:
        _load_saved_keys(env_file)
        provider = live_provider(resolved_key, key_source)
        configured = _default_provider(directory)
        if key_source == "env-file" and configured in KEYED_PROVIDERS and os.environ.get(
                env_var_for_provider(configured)):
            # Several keys saved: the default stays on the provider it was set to.
            provider = configured
        _point_llm_config_at(directory, None, provider=provider)
        console.print(f"[bold]Live mode:[/bold] using {provider}.")

    reload_settings()

    try:
        from nl2sql.cli.demo.playground.app import build_app
    except ImportError:
        print_error(INSTALL_HINT)
        raise SystemExit(1)

    engine = _build_engine()
    questions = list(DEMO_QUESTIONS)
    roles = list(engine.context.policies_cfg.roles)

    if record:
        if _record_all(engine, questions, proxy, store, store_path):
            # The file is written with the runs that succeeded; the exit says
            # that not every question was.
            raise SystemExit(1)
        return

    app = build_app(
        engine,
        questions=questions,
        roles=roles,
        mode=mode if hosted_mode.enabled else ("replay" if replay_server else "live"),
        dataset=DATASET,
        project_dir=directory,
        host=host,
        allow_settings=allow_settings,
        hosted=hosted_mode,
        recorded_questions=recorded_questions,
        questions_by_datasource=DEMO_QUESTIONS_BY_DATASOURCE,
    )
    url = f"http://{host}:{port}/"
    print_success(f"Playground ready at {url}")
    if not (no_browser or hosted_mode.enabled):
        webbrowser.open(url)
    try:
        _serve(app, host, port)
    finally:
        if replay_server:
            replay_server.stop()
