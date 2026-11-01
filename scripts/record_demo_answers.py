"""Records real answers to the demo's guided questions, for the hosted demo's keyless path.

A visitor to the hosted demo who has not pasted a key is answered from these
recordings when they pick a guided question (see ``docs/deployment/hosted-demo.md``).
This script makes them: it builds a fresh demo project and an engine whose
models are an ``nl2sql.llm.replay.Recorder`` over Claude, asks every guided
question, and writes what was recorded to the file the engine ships, ``packages/nl2sql/src/nl2sql/datasets/recordings/chinook.json``.

From the repository root::

    python scripts/record_demo_answers.py

The key is ``ANTHROPIC_API_KEY``, read from the environment or, failing that,
from the repository root's ``.env`` (read with python-dotenv, never loaded into
the environment). The script never prints it and never writes the environment:
it hands the key to the engine in its LLM config, so it wins over any OpenAI
or OpenRouter key that is exported and is saved nowhere, and the answers are
always recorded with the Anthropic preset's default model
(``nl2sql.llm.providers.DEFAULT_MODELS["anthropic"]``). The demo project is a
temporary folder unless ``--dir`` names one.

It spends money: every guided question runs the whole pipeline once (about
five model calls each). The ``Record demo answers`` workflow
(``.github/workflows/record_demo.yml``) runs the same thing in CI from the
``ANTHROPIC_API_KEY`` repository secret and opens a pull request with the result.

A question counts as recorded only when its run succeeded (``record_answers``),
and every question that was not recorded is listed.

Exit status: 0 when every guided question was recorded, 1 when some were not
(the file is still written, with the runs that succeeded), 2 when there is
no key or ``langchain-anthropic`` is not installed.
"""

from __future__ import annotations

import argparse
import importlib.util
import pathlib
import shutil
import sys
import tempfile
from typing import List, Optional

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "packages" / "nl2sql" / "src" / "nl2sql" / "datasets" / "recordings" / "chinook.json"


def _parse(argv: Optional[List[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dir", type=pathlib.Path, default=None,
                        help="demo project to record in (default: a fresh temporary one)")
    parser.add_argument("--out", type=pathlib.Path, default=OUT, help="where the recordings are written")
    parser.add_argument("--env-file", type=pathlib.Path, default=ROOT / ".env",
                        help="dotenv file to read the key from when it is not in the environment")
    return parser.parse_args(argv)


def recording_key(env_file: pathlib.Path) -> Optional[str]:
    """The Claude key: the exported ``ANTHROPIC_API_KEY``, else the one in ``env_file``.

    The file is read, not loaded: nothing is written to the environment.
    """
    from dotenv import dotenv_values

    from nl2sql.llm.providers import api_key_from_env, env_var_for_provider

    key = (api_key_from_env("anthropic") or "").strip()
    if not key and env_file.is_file():
        key = (dotenv_values(env_file).get(env_var_for_provider("anthropic")) or "").strip()
    return key or None


def record_answers(engine, recorder, questions: List[str], *, user_context=None) -> List[str]:
    """Asks ``questions`` through ``engine``, whose models are ``recorder``'s; returns those that were recorded.

    A question counts as recorded only when its run succeeded -- rows, an answer,
    no error. A failed run's recordings are dropped (a run that died in the
    aggregator still left its decomposer and planner calls behind), so the
    store never answers a question the live engine could not.
    """
    recorded = []
    for question in questions:
        try:
            result = engine.run_query(question, user_context=user_context)
            ok = result.status == "success" and bool(result.final_answer) and not result.errors
        except Exception as exc:  # one question failing must not lose the others
            print(f"  failed: {question} ({type(exc).__name__})", file=sys.stderr)
            ok = False
        if ok:
            recorded.append(question)
        else:
            recorder.store.discard(question)
    return recorded


# The conversations recorded for Chat: each guided Chinook question on its own, the scripted follow-ups
# (a second turn that leans on the first) and one question that is ambiguous on purpose.
FOLLOW_UPS = [
    ("Who are the top 5 customers by total spend?", "And the top 10?"),
    ("Which genre sells the most tracks?", "Show that by year instead."),
    ("Which artist has the most albums?", "And which has the fewest?"),
]
CLARIFICATION_QUESTION = "Which customers are our best?"
KEPT = ("answered", "needs_clarification")


def chat_scripts(questions: List[str]) -> List[List[str]]:
    """Each script is the messages of one conversation, in order."""
    return [*[[q] for q in questions], *[list(pair) for pair in FOLLOW_UPS], [CLARIFICATION_QUESTION]]


def record_conversations(engine, recorder, scripts: List[List[str]], *, user_context=None) -> List[str]:
    """Plays each script as a conversation; returns the messages whose turn ended answered or asking which reading.

    A message whose turn failed (or stopped) loses its model steps and every pipeline run it asked for, as a
    failed ``run_query`` does, and ends that conversation: its later messages would lean on a turn that is gone.
    """
    kept = []
    for script in scripts:
        conversation = engine.conversations.start(user_context=user_context)
        for message in script:
            try:
                turn = engine.conversations.ask(conversation.id, message, user_context=user_context)
            except Exception as exc:  # one conversation failing must not lose the others
                print(f"  failed: {message} ({type(exc).__name__})", file=sys.stderr)
                recorder.store.discard(message)
                break
            if turn.status in KEPT:
                kept.append(message)
                continue
            recorder.store.discard(message)
            for step in turn.steps:
                recorder.store.discard(getattr(step, "question", None) or message)
            break
    return kept


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse(argv)
    key = recording_key(args.env_file)
    if not key:
        print("No ANTHROPIC_API_KEY in the environment or in "
              f"{args.env_file}. Nothing was recorded.", file=sys.stderr)
        return 2

    if importlib.util.find_spec("langchain_anthropic") is None:
        from nl2sql.llm.wires.anthropic import EXTRA_HINT

        print(f"Recording uses Claude, which needs langchain-anthropic. {EXTRA_HINT}. "
              "Nothing was recorded.", file=sys.stderr)
        return 2

    from nl2sql import LLM, NL2SQL, UserContext, load_config, prepare_demo
    from nl2sql.datasets.demo import DEMO_QUESTIONS
    from nl2sql.llm.providers import DEFAULT_MODELS, default_temperature_for
    from nl2sql.llm.replay import Recorder
    from nl2sql.registry import Registry

    scratch = None
    directory = args.dir
    if directory is None:
        scratch = tempfile.mkdtemp(prefix="nl2sql-record-")
        directory = pathlib.Path(scratch) / "demo"
    try:
        # The demo project is written with no key (the replay provider); only the engine records, with Claude.
        config_path = prepare_demo(directory, provider="replay")
        registry = Registry.default()
        recorder = Recorder(registry.load("llm", "anthropic"), provider="anthropic")
        registry.add_llm("record", recorder)
        config = load_config(config_path).model_copy(update={"llm": LLM(
            provider="record", model=DEFAULT_MODELS["anthropic"], api_key=key,
            temperature=default_temperature_for("anthropic"))})
        with NL2SQL(config, registry=registry) as engine:
            engine.check()
            caller = UserContext(user_id="recorder", roles=["admin"])
            recorded = record_answers(engine, recorder, list(DEMO_QUESTIONS), user_context=caller)
            chatted = record_conversations(engine, recorder, chat_scripts(list(DEMO_QUESTIONS)), user_context=caller)
    finally:
        if scratch:
            shutil.rmtree(scratch, ignore_errors=True)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    recorder.store.save(args.out)
    missing = [q for q in DEMO_QUESTIONS if q not in recorder.store.covered(DEMO_QUESTIONS)]
    print(f"Recorded {len(recorded)} of {len(DEMO_QUESTIONS)} guided questions and "
          f"{len(chatted)} chat messages to {args.out}")
    for question in missing:
        print(f"  not recorded: {question}")
    return 0 if not missing else 1


if __name__ == "__main__":
    sys.exit(main())
