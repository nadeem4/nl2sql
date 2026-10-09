"""Claude sometimes nests a forced tool call's arguments under one extra key.

A real recording run on ``claude-opus-5`` failed the decomposer twice with::

    2 validation errors for DecomposerResponse: sub_queries Field required;
    combine_groups Field required
    [input_value={'input': {'sub_queries': ..., 'mapped_subqueries': []}}]

and once with the key ``inputs``; a second run found ``query`` and ``dtype``
as well. The root cause was the decomposer's tool and prompt
(``test_decomposer_tool_schema.py``). As a safety net the anthropic wire
unwraps that one shape -- a single key the schema does not declare, holding an
object with every required field and at least one declared one -- and nothing
else. Everything here runs against ``FakeLLMServer``; no key, no network.
"""
from __future__ import annotations

import pytest
from langchain_core.prompts import ChatPromptTemplate

from nl2sql.llm.wires import structured
from nl2sql.llm.wires.anthropic import unwrap_tool_input
from nl2sql.pipeline.nodes.datasource_resolver.schemas import AnswerabilityResponse
from nl2sql.pipeline.nodes.decomposer.schemas import DecomposerResponse
from nl2sql.testing.fake_llm import FakeLLMServer, Rule

DECOMPOSED = {
    "sub_queries": [{
        "id": "sq1", "datasource_id": "chinook", "intent": "How many customers are there",
        "metrics": [{"name": "customer_count", "aggregation": "count"}],
        "filters": [], "group_by": [],
        "expected_schema": [{"name": "customer_count", "dtype": "int"}],
    }],
    "combine_groups": [{"group_id": "g1", "operation": "standalone",
                        "inputs": [{"subquery_id": "sq1"}], "join_keys": []}],
    "post_combine_ops": [], "unmapped_subqueries": [],
}

SCHEMA = {
    "type": "object",
    "properties": {"sub_queries": {"type": "array"}, "combine_groups": {"type": "array"},
                   "post_combine_ops": {"type": "array"}},
    "required": ["sub_queries", "combine_groups"],
}


@pytest.mark.parametrize("key", ["input", "inputs", "query", "dtype", "payload"])
def test_a_payload_wrapped_in_one_undeclared_key_is_unwrapped(key):
    """A second real run wrapped it under ``query`` and ``dtype`` too: the key is arbitrary."""
    assert unwrap_tool_input({key: DECOMPOSED}, SCHEMA) == DECOMPOSED


def test_a_payload_that_fills_the_schema_is_left_alone():
    assert unwrap_tool_input(DECOMPOSED, SCHEMA) == DECOMPOSED


def test_a_declared_input_field_is_never_unwrapped():
    schema = {"type": "object", "properties": {"input": {"type": "object"}}, "required": ["input"]}
    args = {"input": {"sub_queries": [], "combine_groups": []}}

    assert unwrap_tool_input(args, schema) == args


def test_a_wrapped_value_missing_required_fields_is_left_for_validation_to_report():
    args = {"input": {"sub_queries": []}}

    assert unwrap_tool_input(args, SCHEMA) == args


def test_non_objects_siblings_and_unrelated_objects_are_left_alone():
    assert unwrap_tool_input({"input": "text"}, SCHEMA) == {"input": "text"}
    assert unwrap_tool_input({"input": DECOMPOSED, "extra": 1}, SCHEMA) == {"input": DECOMPOSED, "extra": 1}
    # Nothing the schema declares inside: not the answer, so not unwrapped.
    no_required = {"type": "object", "properties": {"reason": {"type": "string"}}}
    assert unwrap_tool_input({"dtype": {"name": "x"}}, no_required) == {"dtype": {"name": "x"}}


PROMPT = ChatPromptTemplate.from_messages([("system", "Answer with the tool."), ("human", "{question}")])


@pytest.mark.parametrize("schema, payload, key", [
    (DecomposerResponse, DECOMPOSED, "input"),
    (DecomposerResponse, DECOMPOSED, "inputs"),
    (DecomposerResponse, DECOMPOSED, "query"),
    (DecomposerResponse, {**DECOMPOSED, "mapped_subqueries": []}, "dtype"),
    (AnswerabilityResponse, {"answerable_datasource_ids": ["chinook"], "reason": "music store"}, "input"),
])
def test_every_structured_call_on_claude_survives_a_wrapped_answer(schema, payload, key):
    """Through the real Claude client and ``structured()``, as every node calls it."""
    pytest.importorskip("langchain_anthropic")
    from nl2sql.llm.wires.anthropic_client import build_claude_client

    server = FakeLLMServer([Rule(schema.__name__, {key: payload})]).start()
    try:
        llm = build_claude_client("claude-opus-5", None, api_key="-".join(["fake", "wrap", "key"]),
                                  base_url=server.anthropic_base_url, tags=["test"])
        result = (PROMPT | structured(llm, schema)).invoke({"question": "How many customers are there?"})
    finally:
        server.stop()

    assert result == schema.model_validate(payload)
