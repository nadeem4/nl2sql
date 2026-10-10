"""A join or compare that lost an input is refused, not answered from one side.

When a sub-query is dropped -- its datasource is restricted for the caller, not
resolved, or unsupported -- the decomposer used to keep its combine group with
the inputs that remained. A ``join`` of one input then reached the engine,
which returned the left frame alone: "customers with an open ticket who spent
the most" answered as "customers who spent the most", with no error.

The check belongs in the decomposer (CLAUDE.md: validation before generation),
before any sub-query runs. The model is ``FakeLLMServer``: no key, no network.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import polars as pl
import pytest

from nl2sql.aggregation.engines.polars_duckdb import PolarsDuckdbEngine
from nl2sql.common.errors import ErrorCode
from nl2sql.llm.wires import WIRES
from nl2sql.pipeline.nodes.datasource_resolver.schemas import DatasourceResolverResponse, ResolvedDatasource
from nl2sql.pipeline.nodes.decomposer.node import DecomposerNode
from nl2sql.pipeline.state import GraphState
from nl2sql.testing.fake_llm import FakeLLMServer, Rule

QUESTION = "Which customers with an open support ticket spent the most on music?"


def _sub_query(sq_id, ds, intent, columns):
    return {"id": sq_id, "datasource_id": ds, "intent": intent, "metrics": [], "filters": [],
            "group_by": [{"attribute": "customer"}],
            "expected_schema": [{"name": c, "dtype": "int"} for c in columns]}


def _decomposition(operation):
    return {
        "sub_queries": [_sub_query("sq_music", "chinook", "music spend per customer", ["customer_id", "spend"]),
                        _sub_query("sq_tickets", "support", "customers with an open ticket",
                                   ["customer_id", "open_tickets"])],
        "combine_groups": [{"group_id": "g1", "operation": operation,
                            "inputs": [{"subquery_id": "sq_music", "role": "left"},
                                       {"subquery_id": "sq_tickets", "role": "right"}],
                            "join_keys": [{"left": "customer_id", "right": "customer_id"}]}],
        "post_combine_ops": [],
        "unmapped_subqueries": [],
    }


def _run(payload, allowed):
    server = FakeLLMServer([Rule("DecomposerResponse", payload)]).start()
    try:
        llm = WIRES["openai"].build_client("gpt-5.4", 0.0, api_key="-".join(["fake", "decomposer", "key"]),
                                           base_url=server.base_url, tags=["decomposer"])
        ctx = SimpleNamespace(llm_registry=MagicMock())
        ctx.llm_registry.get_llm.return_value = llm
        state = GraphState(
            user_query=QUESTION,
            datasource_resolver_response=DatasourceResolverResponse(
                resolved_datasources=[ResolvedDatasource(datasource_id=ds, metadata={})
                                      for ds in ("chinook", "support")],
                allowed_datasource_ids=allowed,
            ),
        )
        result = DecomposerNode(ctx)(state)
    finally:
        server.stop()
    return result, server


@pytest.mark.parametrize("operation", ["join", "compare"])
def test_a_combine_that_lost_an_input_to_rbac_is_refused(operation):
    result, server = _run(_decomposition(operation), allowed=["chinook"])

    [error] = result["errors"]
    assert error.error_code == ErrorCode.PLANNER_FAILED
    assert "g1" in error.message and operation in error.message
    assert "restricted_datasource" in error.message
    # No DAG, so the layer router ends the run before any sub-query executes.
    assert "execution_dag" not in result
    # A permission is not something asking the model again can fix.
    assert len(server.calls) == 1


def test_the_refusal_does_not_name_the_restricted_datasource():
    result, _ = _run(_decomposition("join"), allowed=["chinook"])

    assert "support" not in result["errors"][0].message


def test_a_join_with_both_inputs_still_builds_its_dag():
    result, _ = _run(_decomposition("join"), allowed=["chinook", "support"])

    assert not result.get("errors")
    assert result["execution_dag"] is not None


def test_a_union_that_lost_an_input_still_runs_on_what_remains():
    # A union of what the caller may see is still a correct answer about it;
    # the dropped intent is reported in unmapped_subqueries as before.
    payload = _decomposition("join")
    payload["combine_groups"] = [{"group_id": "g1", "operation": "union",
                                  "inputs": [{"subquery_id": "sq_music"}, {"subquery_id": "sq_tickets"}],
                                  "join_keys": []}]
    result, _ = _run(payload, allowed=["chinook"])

    assert not result.get("errors")
    assert [u.reason for u in result["decomposer_response"].unmapped_subqueries] == ["restricted_datasource"]


@pytest.mark.parametrize("operation", ["join", "compare"])
def test_the_engine_refuses_a_one_sided_join_too(operation):
    # The last line of defence: never return the left frame as the join.
    frame = pl.DataFrame({"customer_id": [1], "spend": [2.0]})

    with pytest.raises(ValueError, match="two inputs"):
        PolarsDuckdbEngine.__new__(PolarsDuckdbEngine).combine(
            operation, [("left", frame)], [{"left": "customer_id", "right": "customer_id"}])
