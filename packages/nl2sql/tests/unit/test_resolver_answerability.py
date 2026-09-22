"""The resolver's single-datasource shortcut and its answerability check.

With one datasource registered the resolver skips the vector search, but the
role check and the answerability check still run. The answerability check is
one small structured LLM call; an empty answer refuses the question before the
decomposer, with ``QUESTION_NOT_ANSWERABLE``.

The model is ``FakeLLMServer``, so what the resolver sent is read off the wire.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from nl2sql.auth import UserContext
from nl2sql.common.errors import ErrorCode, ErrorSeverity
from nl2sql.llm import LLMRegistry
from nl2sql.llm.models import AgentConfig
from nl2sql.pipeline.nodes.datasource_resolver.node import DatasourceResolverNode
from nl2sql.pipeline.routes import resolver_route
from nl2sql.pipeline.state import GraphState
from nl2sql.secrets import SecretManager
from nl2sql.testing.fake_llm import FakeLLMServer, Rule

FAKE_KEY = "-".join(["fake", "key", "for", "tests"])
JUDGE = "AnswerabilityResponse"


def _registry(server, **agents) -> LLMRegistry:
    registry = LLMRegistry(SecretManager())
    registry.register_llm(AgentConfig(provider="openai", model="gpt-4o", api_key=FAKE_KEY,
                                      base_url=server.base_url, name="default"))
    for name, model in agents.items():
        registry.register_llm(AgentConfig(provider="openai", model=model, api_key=FAKE_KEY,
                                          base_url=server.base_url, name=name))
    return registry


def _snapshot(description, tables):
    return SimpleNamespace(
        metadata=SimpleNamespace(description=description),
        contract=SimpleNamespace(tables={
            f"[main].[{t}]": SimpleNamespace(table=SimpleNamespace(schema_name="main", table_name=t))
            for t in tables
        }),
    )


SNAPSHOTS = {
    "chinook": _snapshot("A digital music store: artists, albums, tracks, customers and invoices.",
                         ["Track", "Album", "Artist", "Customer"]),
    "hr": _snapshot("Employees, departments and salaries.", ["Employee", "Department"]),
}


def _doc(ds_id):
    return SimpleNamespace(metadata={"datasource_id": ds_id, "schema_version": "v1"})


class SpyStore:
    """A vector store that records every search."""

    def __init__(self, ds_ids=()):
        self.searches = []
        self.ds_ids = list(ds_ids)

    def retrieve_datasource_candidates(self, query, k=5, explain=None):
        self.searches.append(query)
        return [_doc(ds_id) for ds_id in self.ds_ids]


def _ctx(server, ds_ids, allowed=("*",), vector_store=None, **agents):
    return SimpleNamespace(
        vector_store=vector_store if vector_store is not None else SpyStore(ds_ids),
        rbac=SimpleNamespace(get_allowed_datasources=lambda _ctx: list(allowed)),
        ds_registry=SimpleNamespace(list_ids=lambda: list(ds_ids)),
        schema_store=SimpleNamespace(
            get_latest_version=lambda _id: "v1",
            get_latest_snapshot=lambda ds_id: SNAPSHOTS.get(ds_id),
        ),
        llm_registry=_registry(server, **agents),
    )


def _state(question="How many customers are there?", **kwargs):
    return GraphState(user_query=question, user_context=UserContext(roles=["admin"]), **kwargs)


@pytest.fixture
def serve():
    servers = []

    def _make(*payloads):
        server = FakeLLMServer([Rule(JUDGE, p) for p in payloads]).start()
        servers.append(server)
        return server

    yield _make
    for server in servers:
        server.stop()


ANSWERABLE = {"answerable_datasource_ids": ["chinook"], "reason": "Customers are in chinook."}
UNANSWERABLE = {"answerable_datasource_ids": [], "reason": "Weather is not in any datasource."}


def _prompt(call) -> str:
    return "\n".join(str(m.get("content") or "") for m in call["body"]["messages"])


# --- Row 53: one datasource, no vector search -------------------------------


def test_one_datasource_skips_the_vector_search_but_still_resolves_it(serve):
    server = serve(ANSWERABLE)
    ctx = _ctx(server, ["chinook"])

    result = DatasourceResolverNode(ctx)(_state())

    assert ctx.vector_store.searches == []
    assert "errors" not in result
    response = result["datasource_resolver_response"]
    assert [d.datasource_id for d in response.resolved_datasources] == ["chinook"]
    assert response.allowed_datasource_ids == ["chinook"]
    assert resolver_route(GraphState(user_query="q", datasource_resolver_response=response)) == "continue"


def test_one_datasource_works_without_any_vector_store(serve):
    server = serve(ANSWERABLE)
    ctx = _ctx(server, ["chinook"])
    ctx.vector_store = None

    result = DatasourceResolverNode(ctx)(_state())

    assert "errors" not in result
    assert result["datasource_resolver_response"].allowed_datasource_ids == ["chinook"]


def test_one_datasource_still_refuses_a_denied_role_without_asking_the_model(serve):
    server = serve(ANSWERABLE)
    ctx = _ctx(server, ["chinook"], allowed=())

    result = DatasourceResolverNode(ctx)(_state())

    assert ctx.vector_store.searches == []
    assert result["errors"][0].error_code == ErrorCode.SECURITY_VIOLATION
    assert server.calls == []


def test_two_datasources_run_the_full_vector_resolution(serve):
    server = serve({"answerable_datasource_ids": ["chinook", "hr"], "reason": "Both."})
    ctx = _ctx(server, ["chinook", "hr"])

    result = DatasourceResolverNode(ctx)(_state())

    assert ctx.vector_store.searches == ["How many customers are there?"]
    response = result["datasource_resolver_response"]
    assert sorted(d.datasource_id for d in response.resolved_datasources) == ["chinook", "hr"]


def test_two_datasources_without_a_vector_store_fail_loudly(serve):
    server = serve(ANSWERABLE)
    ctx = _ctx(server, ["chinook", "hr"])
    ctx.vector_store = None

    result = DatasourceResolverNode(ctx)(_state())

    assert result["errors"][0].error_code == ErrorCode.SCHEMA_RETRIEVAL_FAILED
    assert "Vector store unavailable" in result["errors"][0].message


# --- Row 54: answerability ---------------------------------------------------


def test_an_unanswerable_question_is_refused_before_the_decomposer(serve):
    server = serve(UNANSWERABLE)
    ctx = _ctx(server, ["chinook"])

    result = DatasourceResolverNode(ctx)(_state("What is the weather in Paris?"))

    [error] = result["errors"]
    assert error.error_code == ErrorCode.QUESTION_NOT_ANSWERABLE
    assert error.severity == ErrorSeverity.ERROR
    assert "can't be answered" in error.message
    assert resolver_route(GraphState(user_query="q", **{k: v for k, v in result.items()
                                                        if k == "datasource_resolver_response"})) == "end"
    assert [c["name"] for c in server.calls] == [JUDGE]


def test_an_answerable_question_proceeds_and_records_the_reason(serve):
    server = serve(ANSWERABLE)

    result = DatasourceResolverNode(_ctx(server, ["chinook"]))(_state())

    assert "errors" not in result
    assert any("Customers are in chinook." in r["content"] for r in result["reasoning"])


def test_the_explicit_override_is_judged_too(serve):
    server = serve(UNANSWERABLE)
    ctx = _ctx(server, ["chinook", "hr"])

    result = DatasourceResolverNode(ctx)(_state("What is the weather in Paris?", datasource_id="chinook"))

    assert ctx.vector_store.searches == []
    assert result["errors"][0].error_code == ErrorCode.QUESTION_NOT_ANSWERABLE


def test_the_judge_sees_only_the_datasources_the_role_may_read(serve):
    server = serve(ANSWERABLE)
    ctx = _ctx(server, ["chinook", "hr"], allowed=("chinook",))

    DatasourceResolverNode(ctx)(_state())

    prompt = _prompt(server.calls[0])
    assert "chinook" in prompt and '"Customer"' in prompt
    assert "Employee" not in prompt and '"hr"' not in prompt


def test_the_prompt_is_conservative_and_carries_descriptions_and_tables(serve):
    server = serve(ANSWERABLE)

    DatasourceResolverNode(_ctx(server, ["chinook"]))(_state())

    prompt = _prompt(server.calls[0])
    assert "A digital music store" in prompt
    assert '"tables": ["Album", "Artist", "Customer", "Track"]' in prompt
    assert "answerable" in prompt.lower() and "unsure" in prompt.lower()
    assert prompt.rstrip().endswith("How many customers are there?")


def test_the_prompt_is_byte_identical_apart_from_the_trailing_question(serve):
    server = serve({"answerable_datasource_ids": ["chinook", "hr"], "reason": "Both."})
    first = _ctx(server, ["chinook", "hr"])
    # The same datasources, found by the search in the other order.
    second = _ctx(server, ["chinook", "hr"], vector_store=SpyStore(["hr", "chinook"]))

    DatasourceResolverNode(first)(_state("How many customers are there?"))
    DatasourceResolverNode(second)(_state("Which department pays the most?"))

    a, b = (_prompt(c) for c in server.calls)
    # The system message (instructions and datasources) is the cacheable prefix.
    system_a, system_b = (c["body"]["messages"][0] for c in server.calls)
    assert system_a["role"] == "system" and system_a == system_b
    assert a.endswith("How many customers are there?")
    assert b.endswith("Which department pays the most?")
    assert a[: -len("How many customers are there?")] == b[: -len("Which department pays the most?")]


def test_the_judge_uses_its_own_agent_config_when_one_is_set(serve):
    server = serve(ANSWERABLE)
    ctx = _ctx(server, ["chinook"], datasourceresolver="gpt-4.1-mini")

    DatasourceResolverNode(ctx)(_state())

    assert server.calls[0]["body"]["model"] == "gpt-4.1-mini"


def test_the_judge_falls_back_to_the_default_agent(serve):
    server = serve(ANSWERABLE)

    DatasourceResolverNode(_ctx(server, ["chinook"]))(_state())

    assert server.calls[0]["body"]["model"] == "gpt-4o"
