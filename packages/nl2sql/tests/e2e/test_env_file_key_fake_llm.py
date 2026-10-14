"""``nl2sql run`` authenticates with a key kept only in the ``.env`` next to ``nl2sql.yaml``.

The LLM section names the key as ``${env:OPENAI_API_KEY}``, which resolves from
the process environment. The CLI loads the ``.env`` next to the config it was
given into the environment before it builds the engine, never overriding a
variable already set.
"""
from __future__ import annotations

import uuid

import pytest

from .conftest import run_cli
from .recordings_chinook import RULES_COUNT_CUSTOMERS


@pytest.mark.e2e
def test_run_authenticates_with_a_key_only_in_the_env_file(demo_project, fake_llm):
    server, env = fake_llm(RULES_COUNT_CUSTOMERS)
    env.pop("OPENAI_API_KEY", None)
    key = "-".join(["sk", "proj", "envfile" + uuid.uuid4().hex])
    env_file = demo_project / ".env"
    env_file.write_text(f"OPENAI_API_KEY={key}\n", encoding="utf-8")
    try:
        r = run_cli(demo_project, env, "run", "How many customers are there?")
    finally:
        env_file.unlink()

    assert r.returncode == 0, r.stdout + r.stderr
    assert server.calls and all(c["authorization"] == f"Bearer {key}" for c in server.calls)
    assert key not in r.stdout + r.stderr
