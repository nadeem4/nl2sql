"""Records real answers to the demo's guided questions, for the hosted demo's keyless path.

A visitor to the hosted demo who has not pasted a key is answered from these
recordings when they pick a guided question (see ``docs/deployment/hosted-demo.md``).
This script makes them: it runs ``nl2sql demo --record`` against a fresh demo
project with a real Claude model, then copies what was recorded to the file the
engine ships, ``packages/nl2sql/src/nl2sql/cli/demo/recordings/chinook.json``.

From the repository root::

    python scripts/record_demo_answers.py

The key is ``ANTHROPIC_API_KEY``, read from the environment or, failing that,
from the repository root's ``.env`` (read with python-dotenv, never loaded into
the environment). The script never prints it and never writes the environment:
it hands the key to ``nl2sql demo --record`` as ``--api-key`` would, so it wins
over any OpenAI or OpenRouter key that is exported, and the answers are always
recorded with the Anthropic preset's default model
(``nl2sql.llm.providers.DEFAULT_ANTHROPIC_MODEL``). Like ``--api-key``, the
demo saves it to the demo project's ``.env`` -- a temporary folder unless
``--dir`` names one.

It spends money: every guided question runs the whole pipeline once (about
five model calls each). The ``Record demo answers`` workflow
(``.github/workflows/record_demo.yml``) runs the same thing in CI from the
``ANTHROPIC_API_KEY`` repository secret and opens a pull request with the result.

A question counts as recorded only when its run succeeded -- rows, an answer,
no error. A failed run's recordings are dropped (a run that died in the
aggregator still left its decomposer and planner calls behind, and a real run
once reported "Recorded 20 of 20" with half of them failed), and every
question that was not recorded is listed.

Exit status: 0 when every guided question was recorded, 1 when some were not
(the file is still written, with the runs that succeeded) or when the demo stopped before
recording -- its indexing failed, say -- (nothing is written), 2 when there is
no key or ``langchain-anthropic`` is not installed.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import pathlib
import shutil
import sys
import tempfile
import time
from typing import List, Optional

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "packages" / "nl2sql" / "src" / "nl2sql" / "cli" / "demo" / "recordings" / "chinook.json"


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

    from nl2sql.cli import console
    from nl2sql.cli.commands import demo
    from nl2sql.datasets.demo import DEMO_QUESTIONS
    from nl2sql.llm.replay import ReplayStore

    # This script is an entry point of its own, so the CLI's `main()` -- which
    # makes stdout and stderr UTF-8 -- never runs. Redirected to a file on
    # Windows they are cp1252, and the demo's first check mark killed indexing.
    console.configure_output_encoding()

    scratch = None
    directory = args.dir
    if directory is None:
        scratch = tempfile.mkdtemp(prefix="nl2sql-record-")
        directory = pathlib.Path(scratch) / "demo"
    cwd = os.getcwd()
    recorded = directory.resolve() / "recordings.json"
    started = time.time()
    try:
        try:
            # Given as --api-key would give it: it beats any other provider's
            # exported key, and travels in the engine's config, not the environment.
            demo.demo_command(directory=directory, host="127.0.0.1", port=0, no_browser=True, record=True,
                              api_key=key)
        except SystemExit as exc:
            # `demo --record` exits 1 in two cases, after printing why. Either
            # it stopped before recording (indexing failed, say) and wrote
            # nothing, or some questions' runs failed: then it wrote the
            # recordings of the runs that succeeded, and those are kept. A file
            # older than this run is an earlier run's, not this one's.
            if not recorded.is_file() or recorded.stat().st_mtime < started - 1:
                print(f"nl2sql demo --record stopped (exit {exc.code}). Nothing was recorded "
                      f"to {args.out}.", file=sys.stderr)
                return 1
        store = ReplayStore.load(recorded)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        store.save(args.out)
    finally:
        os.chdir(cwd)
        if scratch:
            shutil.rmtree(scratch, ignore_errors=True)

    covered = store.covered(DEMO_QUESTIONS)
    missing = [q for q in DEMO_QUESTIONS if q not in covered]
    print(f"Recorded {len(covered)} of {len(DEMO_QUESTIONS)} guided questions to {args.out}")
    for question in missing:
        print(f"  not recorded: {question}")
    return 0 if not missing else 1


if __name__ == "__main__":
    sys.exit(main())
