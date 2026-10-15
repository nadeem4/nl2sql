"""The per-engine LLM client cache and ``engine.llm.replace``.

docs/architecture/configuration_v2.md, section 4 ("Replacing the LLM at
runtime") and the PR 9 row of section 11.
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import List

import pytest
from langchain_core.embeddings import DeterministicFakeEmbedding
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from nl2sql import LLM, NL2SQL, AgentLLM, ConfigError, Embeddings, Registry
from nl2sql.policies import UserContext
from nl2sql.pipeline.nodes.ast_planner.schemas import PlanModel
from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery
from nl2sql.pipeline.plan_cache import plan_cache_for
from nl2sql.testing.config import make_config

SECRET = "sk-test-0123456789-do-not-print"


class FakeClient(FakeListChatModel):
    """A chat model that records whether it was closed."""

    model_name: str = ""
    closed: bool = False

    def close(self) -> None:
        self.closed = True


class FakeProvider:
    """An LLM provider registered in code; counts every client it builds."""

    interface_version = 2
    label = "Fake"
    api_key_env = None
    requires = ()
    install_hint = ""
    structured_output = "tool_calling"

    def __init__(self) -> None:
        self.built: List[FakeClient] = []

    def create(self, agent):
        client = FakeClient(responses=["ok"], model_name=agent.model)
        self.built.append(client)
        return client


@pytest.fixture
def provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def make_engine(tmp_path: Path, provider: FakeProvider):
    engines = []

    def build(**llm) -> NL2SQL:
        registry = Registry.default()
        registry.add_llm("fake", provider)
        section = {"provider": "fake", "model": "m1", "api_key": SECRET, **llm}
        config = make_config(tmp_path, llm=section).model_copy(
            update={"embeddings": Embeddings(instance=DeterministicFakeEmbedding(size=8))})
        engine = NL2SQL(config, registry=registry)
        engines.append(engine)
        return engine

    yield build
    for engine in engines:
        engine.close()


def _llm(model: str, **fields) -> LLM:
    return LLM(provider="fake", model=model, api_key=SECRET, **fields)


def _llms(engine: NL2SQL):
    return engine._runtime.llm_registry


def test_agents_with_equal_settings_share_one_client(make_engine, provider):
    engine = make_engine(agents={"ast_planner": {"temperature": 0.0}, "refiner": {"temperature": 0.5}})
    llms = _llms(engine)

    shared = llms.get_llm("default")
    assert llms.get_llm("astplanner") is shared
    assert llms.get_llm("decomposer") is shared
    assert llms.get_llm("refiner") is not shared
    assert len(provider.built) == 2


def test_swapping_back_reuses_cached_clients(make_engine, provider):
    engine = make_engine()
    first = _llms(engine).get_llm("astplanner")

    engine.llm.replace(_llm("m2"))
    assert _llms(engine).get_llm("astplanner").model_name == "m2"
    engine.llm.replace(_llm("m1"))

    assert _llms(engine).get_llm("astplanner") is first
    assert len(provider.built) == 2
    assert not first.closed


def test_cache_key_never_contains_the_key(make_engine):
    engine = make_engine()
    keys = list(_llms(engine).current.keys.values())

    assert keys
    for key in keys:
        assert SECRET not in str(key)
        assert SECRET not in repr(key)
        assert all(SECRET not in str(value) for _, value in key.fields)
        assert key.api_key_sha256 and len(key.api_key_sha256) == 64
        assert key.api_key_sha256[:8] in str(key)
        assert key.api_key_sha256 not in str(key)


def test_evicted_client_is_closed_after_its_last_run(make_engine, provider):
    engine = make_engine()
    first = _llms(engine).get_llm("astplanner")
    run = _llms(engine).begin_run()

    # Seventeen models: the first falls out of the LRU of 16 while a run still uses it.
    for i in range(2, 19):
        engine.llm.replace(_llm(f"m{i}"))
    assert not first.closed

    run.end()
    assert first.closed
    # A client evicted while nothing uses it is closed straight away.
    second = next(client for client in provider.built if client.model_name == "m2")
    assert second.closed


def test_in_flight_run_finishes_on_the_old_llm(make_engine, monkeypatch):
    from nl2sql.pipeline import runtime as pipeline_runtime

    engine = make_engine()
    seen = {}

    class Graph:
        def invoke(self, state, config=None):
            # Another caller swaps the LLM while this run is in flight.
            engine.llm.replace(_llm("m2"))
            seen["during"] = seen["planner"].model_name
            return {}

    def build_graph(ctx, execute=True):
        seen["planner"] = ctx.llm_registry.get_llm("astplanner")
        return Graph()

    monkeypatch.setattr(pipeline_runtime, "build_graph", build_graph)
    monkeypatch.setattr(pipeline_runtime, "write_run_trace", lambda *a, **k: None)
    pipeline_runtime.run_with_graph(engine._runtime, "How many customers?")

    assert seen["during"] == "m1"
    assert not seen["planner"].closed
    pipeline_runtime.run_with_graph(engine._runtime, "How many customers?")
    assert seen["planner"].model_name == "m2"


def test_invalid_replacement_changes_nothing(make_engine, monkeypatch):
    engine = make_engine()
    before_config = engine.config
    before_client = _llms(engine).get_llm("astplanner")

    with pytest.raises(ConfigError, match="llm.agents.ast_planner.provider"):
        engine.llm.replace(LLM(provider="fake", model="m2", api_key=SECRET,
                               agents={"ast_planner": AgentLLM(provider="opneai")}))
    monkeypatch.delenv("NL2SQL_TEST_NO_SUCH_KEY", raising=False)
    with pytest.raises(ConfigError, match="llm.api_key"):
        engine.llm.replace(LLM(provider="fake", model="m2", api_key="${env:NL2SQL_TEST_NO_SUCH_KEY}"))

    assert engine.config is before_config
    assert _llms(engine).get_llm("astplanner") is before_client


def test_replace_logs_old_and_new_models_without_keys(make_engine, caplog):
    engine = make_engine()
    records = []
    engine.events.subscribe(lambda event: records.append((event.name, event.data)), names=["llm.changed"])

    with caplog.at_level(logging.INFO, logger="nl2sql.llm"):
        change = engine.llm.replace(
            LLM(provider="fake", model="m2", api_key=SECRET + "-new",
                agents={"ast_planner": AgentLLM(model="m3")}),
            by=UserContext(user_id="u42", roles=["admin"]), via="api")

    lines = [r.getMessage() for r in caplog.records if r.name == "nl2sql.llm"]
    assert len(lines) == 1
    assert "user=u42 roles=[admin] via api" in lines[0]
    assert "default: fake/m1 -> fake/m2" in lines[0]
    assert "ast_planner: fake/m1 -> fake/m3" in lines[0]
    assert len(records) == 1
    event_type, payload = records[0]
    assert event_type == "llm.changed"
    assert payload["by"] == {"user_id": "u42", "roles": ["admin"], "via": "api"}
    assert payload["changes"]["ast_planner"] == {"old": "fake/m1", "new": "fake/m3"}
    assert change.changes == payload["changes"]
    for text in (lines[0], json.dumps(payload), repr(change)):
        assert SECRET not in text
        assert "api_key" not in text
    assert engine.config.llm.model == "m2"


def test_plan_from_another_model_is_never_served(make_engine):
    engine = make_engine()
    runtime = engine._runtime
    sub_query = SubQuery(id="sq1", datasource_id="shop", intent="How many customers?", schema_version="v1")
    plan = PlanModel.model_validate({
        "query_type": "READ",
        "tables": [{"name": "customers", "alias": "t1"}],
        "select_items": [{"alias": "n", "expr": {"kind": "func", "func_name": "COUNT", "is_aggregate": True,
                                                  "args": [{"kind": "column", "alias": "t1",
                                                            "column_name": "id"}]}}],
        "reasoning": "count",
    })
    assert plan_cache_for(runtime).put(sub_query, plan)

    engine.llm.replace(LLM(provider="fake", model="m1", api_key=SECRET,
                           agents={"ast_planner": AgentLLM(model="m2")}))
    assert plan_cache_for(runtime).get(sub_query) is None

    engine.llm.replace(_llm("m1"))
    assert plan_cache_for(runtime).get(sub_query) == plan
