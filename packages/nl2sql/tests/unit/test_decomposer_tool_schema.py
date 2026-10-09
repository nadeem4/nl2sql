"""The decomposer's tool, as Claude sees it, asks to be filled directly.

A recording run on ``claude-opus-5`` had Claude answer the forced
``DecomposerResponse`` tool call with its arguments nested under an arbitrary
single key -- ``input``, ``inputs``, ``query``, ``dtype`` -- on 9 of 20
questions. No other node did. What set the decomposer apart, dumped below:

- its tool went out with ``"description": ""`` (``DecomposerResponse`` had no
  docstring) and no field descriptions, where every other node's tool says what
  it is;
- its prompt asked for free text -- "Return JSON exactly matching this
  structure", "Output JSON only" -- not for a tool call, and labelled the
  question's message ``INPUTS`` / ``User Query``.

So Claude was told to produce a JSON document and handed a tool with no
explanation, and put the document *inside* the tool's arguments.
"""
from __future__ import annotations

import pytest

from nl2sql.pipeline.nodes.decomposer.prompts import DECOMPOSER_SYSTEM_PROMPT
from nl2sql.pipeline.nodes.decomposer.schemas import DecomposerResponse

TOP_LEVEL = ["sub_queries", "combine_groups", "post_combine_ops", "unmapped_subqueries"]


@pytest.fixture
def tool():
    """The tool definition the anthropic wire sends for the decomposer."""
    pytest.importorskip("langchain_anthropic")
    from langchain_anthropic.chat_models import convert_to_anthropic_tool

    return convert_to_anthropic_tool(DecomposerResponse)


def test_the_tool_says_its_arguments_are_the_decomposition_itself(tool):
    description = tool["description"]

    assert description, "an empty tool description gave Claude nothing to go on"
    for name in TOP_LEVEL:
        assert name in description
    assert "top level" in description.lower()
    assert "do not wrap" in description.lower()


def test_every_top_level_field_is_described(tool):
    properties = tool["input_schema"]["properties"]

    assert list(properties) == TOP_LEVEL
    for name in TOP_LEVEL:
        assert properties[name].get("description"), name


def test_the_prompt_asks_for_a_tool_call_not_a_json_document():
    prompt = DECOMPOSER_SYSTEM_PROMPT

    assert "Output JSON only" not in prompt
    assert "Return JSON exactly matching" not in prompt
    assert "DecomposerResponse" in prompt
    assert "top level" in prompt.lower()
    assert "do not wrap" in prompt.lower()
