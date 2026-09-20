"""Per-node wall-clock timings are collected through a LangChain callback."""

import uuid

from nl2sql.pipeline.timing import NodeTimingCallback


def test_records_elapsed_per_named_chain():
    cb = NodeTimingCallback()
    run = uuid.uuid4()
    cb.on_chain_start({"name": "ast_planner"}, {}, run_id=run, name="ast_planner")
    cb.on_chain_end({}, run_id=run)
    assert set(cb.timings) == {"ast_planner"}
    assert cb.timings["ast_planner"] >= 0.0


def test_prefers_the_langgraph_node_name_from_metadata():
    cb = NodeTimingCallback()
    run = uuid.uuid4()
    cb.on_chain_start(
        {"name": "LangGraph"},
        {},
        run_id=run,
        name="LangGraph",
        metadata={"langgraph_node": "logical_validator"},
    )
    cb.on_chain_end({}, run_id=run)
    assert set(cb.timings) == {"logical_validator"}


def test_unfinished_run_is_ignored():
    cb = NodeTimingCallback()
    cb.on_chain_start({"name": "generator"}, {}, run_id=uuid.uuid4(), name="generator")
    assert cb.timings == {}
