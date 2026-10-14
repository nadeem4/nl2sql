"""Evaluation and trace replay run on a runtime of their own, never on the engine's.

They need different settings for one run: tier 2 turns the plan cache off,
replay tells the prompts the date the recording was told. They build a
run-scoped runtime from a derived config (``Runtime.derive``) and leave the
engine's config, LLM set and curated answers alone, so a query running on
the engine meanwhile sees its own settings.
docs/architecture/configuration_v2.md, section 8.
"""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest
from langchain_core.embeddings import FakeEmbeddings

from nl2sql.api.query_api import QueryResult
from nl2sql.config import LLM, Embeddings
from nl2sql.llm.models import AgentConfig, LLMFileConfig
from nl2sql.evaluation import tier2
from nl2sql.runtime import Runtime
from nl2sql.testing.config import make_config


KEY = {"api_key": "${env:RUN_SCOPED_FAKE_KEY}"}


@pytest.fixture(autouse=True)
def _fake_key(monkeypatch):
    monkeypatch.setenv("RUN_SCOPED_FAKE_KEY", "sk-fake-run-scoped")


def _engine(folder) -> Runtime:
    config = make_config(folder, llm={"provider": "openai", "model": "gpt-test", **KEY},
                         policies={"allow_all": True, "refusal_names_tables": True},
                         settings={"pipeline": {"plan_cache": True}})
    return Runtime.from_config(config.model_copy(update={"embeddings": Embeddings(instance=FakeEmbeddings(size=8))}))


def test_derive_changes_the_run_and_never_the_engine(tmp_path):
    engine = _engine(tmp_path)
    before = engine.config

    run = engine.derive({"settings.pipeline.plan_cache": False, "curated": None})

    assert run.settings.pipeline.plan_cache is False and run.curated is None
    assert engine.config is before and engine.settings.pipeline.plan_cache is True
    assert run.index is engine.index and run.ds_registry is engine.ds_registry


def test_the_run_has_its_own_llm_set_through_the_engine_s_client_cache(tmp_path):
    engine = _engine(tmp_path)
    run = engine.derive({})

    assert run.llm_registry is not engine.llm_registry
    assert run.llm_registry.current is not engine.llm_registry.current
    # Equal agent settings: one client, from the one cache.
    assert run.llm_registry.get_llm("default") is engine.llm_registry.get_llm("default")

    run.llm_registry.replace_llms({"default": AgentConfig(provider="openai", model="gpt-5.4", **KEY)})
    assert engine.llm_registry.current.config_for("default").model == "gpt-test"


def test_a_replaced_engine_llm_is_what_a_later_run_starts_from(tmp_path):
    engine = _engine(tmp_path)
    engine.replace_llm(LLM(provider="openai", model="gpt-replaced", **KEY))

    run = engine.derive({"settings.pipeline.plan_cache": False})

    assert run.llm_registry.current.config_for("default").model == "gpt-replaced"
    assert run.llm_registry.get_llm("default") is engine.llm_registry.get_llm("default")


def test_tier2_runs_without_touching_another_engine(tmp_path, monkeypatch):
    a, b = _engine(tmp_path / "a"), _engine(tmp_path / "b")
    a_config, b_config = a.config, b.config
    a_default = a.llm_registry.get_llm_config("default")
    seen = []

    class _Runner:
        def __init__(self, config, ctx, workers, before_case):
            self.ctx = ctx

        def cases(self):
            return [(SimpleNamespace(id="q0", question="?", tags=[], difficulty="easy"), "admin")]

        def run_case(self, question, role):
            # What another query on either engine would read while tier 2 runs.
            seen.append((self.ctx.settings.pipeline.plan_cache, self.ctx.config.policies.refusal_names_tables,
                         a.config is a_config, b.config is b_config))
            return ({"id": question.id, "question": "?", "role": role, "expected": "allowed", "status": "pass",
                     "reason": "", "sql": "SELECT 1", "rows": 1, "gold_rows": 1}, QueryResult())

    monkeypatch.setattr(tier2, "BenchmarkRunner", _Runner)
    cfg = LLMFileConfig(default=AgentConfig(provider="openai", model="gpt-5.4"))
    tier2.run_tier2(a, SimpleNamespace(dataset_path="gold.yaml", roles=["admin"]), {"x": cfg}, max_cost=1.0)

    assert seen == [(False, False, True, True)]
    assert a.config is a_config and b.config is b_config
    assert a.llm_registry.get_llm_config("default") == a_default  # the run's LLMs were its own


def test_replay_pins_the_date_in_its_own_runtime(tmp_path, monkeypatch):
    from nl2sql.pipeline import runtime as pipeline_runtime
    from nl2sql.tracing.replay import replay_trace

    engine = _engine(tmp_path)
    before = engine.config
    seen = {}

    def _run(ctx, question, **_kwargs):
        seen.update(ctx=ctx, today=ctx.settings.pipeline.today, delay=ctx.settings.pipeline.retries.base_delay_s,
                    engine_config=engine.config)
        return {"trace_id": "t1"}

    monkeypatch.setattr(pipeline_runtime, "run_with_graph", _run)
    replay_trace({"request": {"question": "How many customers?", "today": "2024-01-02"}, "nodes": [],
                  "result": {}}, engine)

    assert seen["today"] == dt.date(2024, 1, 2) and seen["delay"] == 0.0
    assert seen["ctx"] is not engine
    assert seen["engine_config"] is before and engine.config is before
    assert engine.settings.pipeline.today is None
