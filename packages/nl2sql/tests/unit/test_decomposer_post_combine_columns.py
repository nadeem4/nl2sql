"""A post-combine op reading a column the combine will not produce is retried, not run.

A recording run asked "Which customers bought jazz tracks but never rock?". The
decomposer split it into two sub-queries, each producing ``customer``, joined
them, and added a post-combine filter on ``bought_rock``. Both scans ran; then
the aggregation engine stopped the run with "Post-combine operation references
'bought_rock', which the combined result does not have. Available columns:
customer." Nothing after the decomposer can retry a decomposition, so the
check has to happen in the decomposer, where the model can be asked again
(CLAUDE.md: validation before generation).

The model is ``FakeLLMServer`` on the openai wire: no key, no network.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import polars as pl
import pytest

from nl2sql.aggregation.columns import combined_columns, input_order
from nl2sql.aggregation.engines.polars_duckdb import PolarsDuckdbEngine
from nl2sql.llm.wires import WIRES
from nl2sql.pipeline.nodes.datasource_resolver.schemas import DatasourceResolverResponse, ResolvedDatasource
from nl2sql.pipeline.nodes.decomposer.node import DecomposerNode
from nl2sql.pipeline.state import GraphState
from nl2sql.testing.fake_llm import FakeLLMServer, Rule

QUESTION = "Which customers bought jazz tracks but never rock?"


def _sub_query(sq_id, intent, columns):
    return {"id": sq_id, "datasource_id": "chinook", "intent": intent,
            "metrics": [], "filters": [], "group_by": [{"attribute": "customer"}],
            "expected_schema": [{"name": c, "dtype": "string"} for c in columns]}


def _decomposition(post_filter_attribute, expected_schema=("customer",)):
    return {
        "sub_queries": [_sub_query("sq_jazz", "customers who bought jazz tracks", expected_schema),
                        _sub_query("sq_rock", "customers who bought rock tracks", expected_schema)],
        "combine_groups": [{"group_id": "g1", "operation": "join",
                            "inputs": [{"subquery_id": "sq_jazz", "role": "left"},
                                       {"subquery_id": "sq_rock", "role": "right"}],
                            "join_keys": [{"left": "customer", "right": "customer"}]}],
        "post_combine_ops": [{"op_id": "op1", "target_group_id": "g1", "operation": "filter",
                              "filters": [{"attribute": post_filter_attribute, "operator": "=",
                                           "value": False}]}],
        "unmapped_subqueries": [],
    }


BAD = _decomposition("bought_rock")
GOOD = _decomposition("customer")


def _run(payload):
    server = FakeLLMServer([Rule("DecomposerResponse", payload)]).start()
    try:
        llm = WIRES["openai"].build_client("gpt-5.4", 0.0, api_key="-".join(["fake", "decomposer", "key"]),
                                           base_url=server.base_url, tags=["decomposer"])
        ctx = SimpleNamespace(llm_registry=MagicMock())
        ctx.llm_registry.get_llm.return_value = llm
        state = GraphState(
            user_query=QUESTION,
            datasource_resolver_response=DatasourceResolverResponse(
                resolved_datasources=[ResolvedDatasource(datasource_id="chinook", metadata={})],
                allowed_datasource_ids=["chinook"],
            ),
        )
        result = DecomposerNode(ctx)(state)
    finally:
        server.stop()
    prompts = ["\n".join(str(m.get("content") or "") for m in c["body"]["messages"]) for c in server.calls]
    return result, prompts


def test_a_post_combine_op_on_a_column_the_combine_lacks_is_asked_again():
    result, prompts = _run(lambda prompt: GOOD if "bought_rock" in prompt else BAD)

    assert len(prompts) == 2
    assert "'bought_rock'" in prompts[1] and "Available columns: customer" in prompts[1]
    assert not result.get("errors")
    assert result["execution_dag"] is not None
    [op] = result["decomposer_response"].post_combine_ops
    assert op.filters[0].attribute == "customer"


def test_a_second_bad_answer_ends_the_run_in_the_decomposer_with_the_reason():
    result, prompts = _run(BAD)

    assert len(prompts) == 2  # one retry, not a loop
    assert "execution_dag" not in result
    [error] = result["errors"]
    assert error.node == "decomposer"
    assert "bought_rock" in error.message and "Available columns: customer" in error.message


def test_a_good_answer_is_asked_for_once():
    result, prompts = _run(GOOD)

    assert len(prompts) == 1
    assert not result.get("errors")


def test_without_an_expected_schema_the_columns_are_unknown_and_nothing_is_rejected():
    result, prompts = _run(_decomposition("bought_rock", expected_schema=()))

    assert len(prompts) == 1
    assert not result.get("errors")


# --- the prediction matches the engine -------------------------------------------


@pytest.mark.parametrize("operation, frames, join_keys", [
    ("standalone", [{"customer": ["a"], "total": [1]}], []),
    ("union", [{"customer": ["a"]}, {"customer": ["b"]}], []),
    ("join", [{"customer": ["a"]}, {"customer": ["a"]}], [{"left": "customer", "right": "customer"}]),
    ("join", [{"customer": ["a"], "total": [1]}, {"customer": ["a"], "total": [2], "genre": ["x"]}],
     [{"left": "customer", "right": "right.customer"}]),
    ("join", [{"id": ["a"], "n": [1]}, {"cust": ["a"], "n": [2]}], [{"left": "id", "right": "cust"}]),
    ("compare", [{"customer": ["a"], "total": [1]}, {"customer": ["a"], "total": [2]}],
     [{"left": "customer", "right": "customer"}]),
])
def test_combined_columns_are_the_columns_the_engine_produces(operation, frames, join_keys):
    engine = PolarsDuckdbEngine.__new__(PolarsDuckdbEngine)
    inputs = [(str(i), pl.DataFrame(f)) for i, f in enumerate(frames)]

    produced = engine.combine(operation, inputs, join_keys)

    assert combined_columns(operation, [list(f) for f in frames], join_keys) == produced.columns


def test_inputs_are_ordered_left_hand_roles_first():
    ordered = sorted([("right", "sq_a"), ("left", "sq_b")], key=lambda r: input_order(*r))

    assert ordered == [("left", "sq_b"), ("right", "sq_a")]
