"""The resolver forwards only what the answerability verdict accepted, and the
decomposer sees each forwarded datasource's description and tables.

The model is ``FakeLLMServer``: what each node sent is read off the wire.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from nl2sql.policies import UserContext
from nl2sql.common.errors import ErrorCode
from nl2sql.indexing.models import RouteCandidate
from nl2sql.llm import LLMRegistry
from nl2sql.llm.models import AgentConfig
from nl2sql.pipeline.nodes.datasource_resolver.node import DatasourceResolverNode
from nl2sql.pipeline.nodes.decomposer.node import DecomposerNode
from nl2sql.pipeline.state import GraphState
from nl2sql.secrets import SecretManager
from nl2sql.testing.fake_llm import FakeLLMServer, Rule
from nl2sql.testing.runtime import fake_runtime

FAKE_KEY = "-".join(["fake", "key", "for", "tests"])
QUESTION = "How many customers are there?"
EMPTY_DECOMPOSITION = {"sub_queries": [], "combine_groups": [], "post_combine_ops": [], "unmapped_subqueries": []}


def _snapshot(description, tables):
    return SimpleNamespace(
        metadata=SimpleNamespace(description=description),
        contract=SimpleNamespace(tables={
            f"[main].[{t}]": SimpleNamespace(table=SimpleNamespace(schema_name="main", table_name=t))
            for t in tables
        }),
    )


SNAPSHOTS = {
    "chinook": _snapshot("A digital music store.", ["Track", "Customer", "Album"]),
    "hr": _snapshot("Employees, departments and salaries.", ["Employee", "Department"]),
    "big": _snapshot("A warehouse with many tables.", [f"T{i:03d}" for i in range(45)]),
}


class Store:
    def __init__(self, ds_ids):
        self.ds_ids = list(ds_ids)

    def route(self, question, datasource_ids, explain=None, **_settings):
        """Ranks the datasources it is asked about in ``ds_ids`` order."""
        return [RouteCandidate(datasource_id=d, score=1.0) for d in self.ds_ids if d in datasource_ids]

    def schema_version(self, ds_id):
        return "v1"

    def snapshot(self, ds_id):
        return SNAPSHOTS.get(ds_id)


def _ctx(server, ds_ids, allowed=("*",)):
    registry = LLMRegistry(SecretManager())
    registry.register_llm(AgentConfig(provider="openai", model="gpt-4o", api_key=FAKE_KEY,
                                      base_url=server.base_url, name="default"))
    return fake_runtime(
        vector_store=Store(ds_ids),
        policies={"default_role": "r", "roles": {role: {"description": "d", "allow": list(list(allowed))} for role in ("r", "admin")}},
        ds_registry=SimpleNamespace(list_ids=lambda: list(ds_ids)),
        llm_registry=registry,
    )


@pytest.fixture
def serve():
    servers = []

    def _make(answerable, reason="ok"):
        server = FakeLLMServer([
            Rule("AnswerabilityResponse", {"answerable_datasource_ids": answerable, "reason": reason}),
            Rule("DecomposerResponse", EMPTY_DECOMPOSITION),
        ]).start()
        servers.append(server)
        return server

    yield _make
    for server in servers:
        server.stop()


def _route(server, ds_ids, allowed=("*",), **state_kwargs):
    """Runs the resolver then the decomposer; returns (resolver output, decomposer prompt)."""
    ctx = _ctx(server, ds_ids, allowed)
    state = GraphState(user_query=QUESTION, user_context=UserContext(roles=["admin"]), **state_kwargs)
    resolved = DatasourceResolverNode(ctx)(state)
    assert "errors" not in resolved, resolved.get("errors")
    state = state.model_copy(update={"datasource_resolver_response": resolved["datasource_resolver_response"]})
    DecomposerNode(ctx)(state)
    call = [c for c in server.calls if c["name"] == "DecomposerResponse"][-1]
    prompt = "\n".join(str(m.get("content") or "") for m in call["body"]["messages"])
    return resolved, prompt


# --- B6: the verdict's subset is what goes forward ---------------------------


def test_a_candidate_the_verdict_rejects_never_reaches_the_decomposer(serve):
    server = serve(["chinook"])

    resolved, prompt = _route(server, ["chinook", "hr"])

    response = resolved["datasource_resolver_response"]
    assert [d.datasource_id for d in response.resolved_datasources] == ["chinook"]
    assert response.allowed_datasource_ids == ["chinook"]
    assert "chinook" in prompt
    assert '"hr"' not in prompt and "Employee" not in prompt


def test_the_verdict_cannot_add_a_datasource_the_role_may_not_read(serve):
    # The judge never saw "hr", but a verdict naming it must not let it through.
    server = serve(["chinook", "hr"])

    resolved, prompt = _route(server, ["chinook", "hr"], allowed=("chinook",))

    response = resolved["datasource_resolver_response"]
    assert response.allowed_datasource_ids == ["chinook"]
    assert [d.datasource_id for d in response.resolved_datasources] == ["chinook"]
    assert "Employee" not in prompt


def test_a_verdict_naming_only_unknown_ids_is_refused(serve):
    server = serve(["weather"])
    ctx = _ctx(server, ["chinook", "hr"])

    result = DatasourceResolverNode(ctx)(GraphState(user_query=QUESTION, user_context=UserContext(roles=["admin"])))

    assert result["errors"][0].error_code == ErrorCode.QUESTION_NOT_ANSWERABLE


def test_both_datasources_go_forward_when_the_verdict_accepts_both(serve):
    server = serve(["hr", "chinook"])

    resolved, _ = _route(server, ["chinook", "hr"])

    response = resolved["datasource_resolver_response"]
    assert sorted(response.allowed_datasource_ids) == ["chinook", "hr"]
    assert sorted(d.datasource_id for d in response.resolved_datasources) == ["chinook", "hr"]


# --- B7: the decomposer sees descriptions and tables ---------------------------


def test_the_decomposer_prompt_carries_descriptions_and_sorted_table_names(serve):
    server = serve(["chinook", "hr"])

    _, prompt = _route(server, ["chinook", "hr"])

    assert "A digital music store." in prompt
    assert "Employees, departments and salaries." in prompt
    assert '"tables":["Album","Customer","Track"]' in prompt
    assert '"tables":["Department","Employee"]' in prompt


def test_the_override_still_gets_context(serve):
    server = serve(["hr"])

    _, prompt = _route(server, ["chinook", "hr"], datasource_id="hr")

    assert "Employees, departments and salaries." in prompt
    assert '"tables":["Department","Employee"]' in prompt
    assert "A digital music store." not in prompt


def test_a_single_datasource_still_gets_context(serve):
    server = serve(["chinook"])

    _, prompt = _route(server, ["chinook"])

    assert "A digital music store." in prompt
    assert '"tables":["Album","Customer","Track"]' in prompt


def test_a_large_schema_is_capped_with_a_count_of_the_rest(serve):
    server = serve(["big"])

    _, prompt = _route(server, ["big"])

    assert '"T029"' in prompt and '"T030"' not in prompt
    assert '"more_tables":15' in prompt


def test_the_decomposer_prompt_is_the_same_whatever_order_the_search_returned(serve):
    server = serve(["chinook", "hr"])
    _, first = _route(server, ["chinook", "hr"])
    _, second = _route(server, ["hr", "chinook"])

    assert first == second
