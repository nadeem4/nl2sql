"""The described pipeline must be the pipeline that runs.

``nl2sql.pipeline.steps`` is what the playground's Pipeline page reads: every
node of a run, in order, with the five that call a model marked. A list like
that rots the moment someone adds a node, so these tests build both real
graphs and compare their node sets against the description. Adding, renaming
or removing a node fails here until the description follows.
"""
from types import SimpleNamespace

import pytest

from nl2sql.llm.providers import LLM_AGENTS
from nl2sql.pipeline.steps import PIPELINE_STEPS, SQL_AGENT_STEP, describe_pipeline


def _blank(_ctx=None):
    """A node that does nothing: the graph's shape is what is under test."""
    return lambda state: {}


@pytest.fixture
def graph_nodes(monkeypatch):
    """The node names both compiled graphs register, with the subgraph's nested."""
    from nl2sql.pipeline import graph as control
    from nl2sql.pipeline.subgraphs import sql_agent

    for name in ("DatasourceResolverNode", "DecomposerNode", "EngineAggregatorNode",
                 "AnswerSynthesizerNode"):
        monkeypatch.setattr(control, name, _blank)
    for name in ("SchemaRetrieverNode", "ASTPlannerNode", "LogicalValidatorNode",
                 "GeneratorNode", "ExecutorNode", "RefinerNode"):
        monkeypatch.setattr(sql_agent, name, _blank)

    ctx = SimpleNamespace()
    top = set(control.build_graph(ctx, execute=True).nodes)
    inner = set(sql_agent.build_sql_agent_graph(ctx, execute=True).nodes)
    # LangGraph adds its own entry node; it is not a step of the pipeline.
    return {name for name in top if not name.startswith("__")}, \
           {name for name in inner if not name.startswith("__")}


def test_every_graph_node_is_described_and_nothing_else_is(graph_nodes):
    top, inner = graph_nodes

    described = {step.node for step in PIPELINE_STEPS}

    # The subgraph is one node of the control graph and a step of its own; its
    # children are described under it.
    assert described == (top | inner)


def test_the_subgraph_children_are_the_steps_nested_under_it(graph_nodes):
    _, inner = graph_nodes

    nested = {step.node for step in PIPELINE_STEPS if step.parent == SQL_AGENT_STEP}

    assert nested == inner


def test_the_model_steps_are_exactly_the_registered_llm_agents():
    agents = {step.node: step.agent for step in PIPELINE_STEPS if step.agent}

    assert agents == LLM_AGENTS


def test_every_step_says_what_it_is_and_what_it_decides():
    for step in PIPELINE_STEPS:
        assert step.label and step.label[0].isupper(), step.node
        assert step.does.endswith("."), step.node
        assert step.kind in {"model", "code"}
        assert (step.kind == "model") == bool(step.agent), step.node


def test_the_order_is_the_order_a_run_takes_them():
    order = [step.node for step in PIPELINE_STEPS]

    assert order.index("datasource_resolver") < order.index("decomposer")
    assert order.index("decomposer") < order.index("sql_agent")
    assert order.index("sql_agent") < order.index("ast_planner")  # nested under it
    assert order.index("ast_planner") < order.index("logical_validator")
    assert order.index("executor") < order.index("aggregator")
    assert order[-1] == "answer_synthesizer"


def test_describe_pipeline_carries_the_configured_model_for_each_model_step():
    configured = {"astplanner": {"provider": "anthropic", "model": "claude-opus-5"}}

    described = describe_pipeline(configured, default={"provider": "openai", "model": "gpt-5.4"})

    by_node = {step["node"]: step for step in described}
    assert by_node["ast_planner"]["provider"] == "anthropic"
    assert by_node["ast_planner"]["model"] == "claude-opus-5"
    # A step with no entry of its own runs on the default agent.
    assert by_node["decomposer"]["provider"] == "openai"
    assert by_node["decomposer"]["model"] == "gpt-5.4"
    # A deterministic step names no model at all.
    assert by_node["generator"]["provider"] is None
    assert by_node["generator"]["model"] is None
    assert by_node["generator"]["kind"] == "code"
