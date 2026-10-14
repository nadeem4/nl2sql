"""Two engines in one process never share anything.

docs/architecture/configuration_v2.md, section 8. Written in PR 7a as a strict
expected failure, while the pipeline read the process-global ``settings`` and
the second engine's row limit leaked into the first. It passes since PR 11
moved the pipeline onto each engine's runtime.
"""
from __future__ import annotations

import os

# Imported first: polars sets _RJEM_MALLOC_CONF in the environment when it is
# first imported, which is the library's write, not the engine's.
import polars  # noqa: F401
import pytest

from nl2sql import LLM, NL2SQL, UserContext
from nl2sql.config import demo_config
from nl2sql.testing.fake_llm import FakeLLMServer

from .recordings_chinook import RULES_ALBUMS_PER_ARTIST

QUESTION = "How many albums does each artist have?"
ADMIN = UserContext(roles=["admin"])


def _engine(folder, server, *, row_limit: int, plan_cache: bool) -> NL2SQL:
    cfg = demo_config(path=folder, llm=LLM(provider="openai", model="gpt-4o", base_url=server.base_url,
                                           api_key="sk-" + "fake-two-engines"))
    settings = cfg.settings.model_copy(update={
        "results": cfg.settings.results.model_copy(update={"row_limit": row_limit}),
        "pipeline": cfg.settings.pipeline.model_copy(update={"plan_cache": plan_cache}),
    })
    return NL2SQL(cfg.model_copy(update={"settings": settings, "curated": None}))


@pytest.mark.e2e
def test_two_engines_in_one_process_keep_their_own_config(tmp_path):
    env_before = dict(os.environ)
    server_a = FakeLLMServer(RULES_ALBUMS_PER_ARTIST).start()
    server_b = FakeLLMServer(RULES_ALBUMS_PER_ARTIST).start()
    try:
        a = _engine(tmp_path / "a", server_a, row_limit=5, plan_cache=False)
        b = _engine(tmp_path / "b", server_b, row_limit=50, plan_cache=True)
        with a, b:
            a.index.build(["chinook"])
            b.index.build(["chinook"])
            ra = a.run_query(QUESTION, user_context=ADMIN)
            rb = b.run_query(QUESTION, user_context=ADMIN)
    finally:
        server_a.stop()
        server_b.stop()

    # each engine's own settings
    assert [r.row_count for r in ra.result_manifest] == [5]
    assert [r.row_count for r in rb.result_manifest] == [50]
    assert (tmp_path / "a" / "index").exists() and (tmp_path / "b" / "index").exists()
    assert server_a.calls and server_b.calls  # each engine's own LLM
    assert a._runtime.embedder is not b._runtime.embedder
    assert dict(os.environ) == env_before  # nothing written
