"""Results at any size, step 1: the answer model sees a preview, and a cut result says so.

The answer synthesizer used to put the whole aggregated result in its prompt
and ask for a Markdown table of it, which is why the row cap was 1,000. Now it
gets the first ``ANSWER_PREVIEW_ROWS`` rows, the total row count, the column
names and a flag saying the preview is partial. The row cap is a resource
ceiling instead (``RESULT_ROW_LIMIT``, 100,000), and a result that reaches it
is flagged as truncated on the result and in the answer.
"""
from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

from nl2sql.api.query_api import result_from_state
from nl2sql.config import ResultSettings
from nl2sql.execution.contracts import ArtifactRef, ExecutorResponse
from nl2sql.pipeline.graph_utils import wrap_subgraph
from nl2sql.pipeline.nodes.aggregator.schemas import AggregatorResponse
from nl2sql.pipeline.nodes.answer_synthesizer.node import AnswerSynthesizerNode, result_preview
from nl2sql.pipeline.nodes.answer_synthesizer.prompts import ANSWER_SYNTHESIZER_PROMPT
from nl2sql.pipeline.nodes.answer_synthesizer.schemas import AggregatedResponse
from nl2sql.pipeline.nodes.ast_planner.schemas import ASTPlannerResponse, Expr, PlanModel, SelectItem, TableRef
from nl2sql.pipeline.nodes.decomposer.schemas import DecomposerResponse, SubQuery
from nl2sql.pipeline.nodes.generator.node import GeneratorNode
from nl2sql.pipeline.nodes.generator.schemas import GeneratorResponse
from nl2sql.pipeline.state import GraphState, SubgraphExecutionState
from nl2sql.pipeline.subgraphs.schemas import SubgraphOutput
from nl2sql.testing.runtime import fake_runtime


def _rows(n):
    return [{"track": f"t{i}", "ms": i} for i in range(n)]


# --- the preview -----------------------------------------------------------

def test_a_long_result_is_previewed_with_its_count_and_marked_partial():
    preview = result_preview({"sq1": _rows(50)}, preview_rows=20)

    assert preview["sq1"]["rows"] == _rows(20)
    assert preview["sq1"]["row_count"] == 50
    assert preview["sq1"]["columns"] == ["track", "ms"]
    assert preview["sq1"]["partial"] is True


def test_a_short_result_is_shown_whole():
    preview = result_preview({"sq1": _rows(5)}, preview_rows=20)

    assert preview["sq1"]["rows"] == _rows(5)
    assert preview["sq1"]["row_count"] == 5
    assert preview["sq1"]["partial"] is False


def test_an_empty_result_has_no_rows_and_is_not_partial():
    preview = result_preview({"sq1": []}, preview_rows=20)

    assert preview["sq1"] == {"rows": [], "row_count": 0, "columns": [], "partial": False}


def _node(preview_rows=20):
    ctx = fake_runtime(llm_registry=MagicMock(), settings={"results": {"preview_rows": preview_rows}})
    node = AnswerSynthesizerNode(ctx)
    node.chain = MagicMock()
    node.chain.invoke.return_value = AggregatedResponse(summary="ok", format_type="text", content="ok")
    return node


def _sent(node):
    return node.chain.invoke.call_args.args[0]


def test_the_answer_model_is_sent_only_the_preview():
    node = _node(preview_rows=20)

    node(GraphState(user_query="q", aggregator_response=AggregatorResponse(terminal_results={"sq1": _rows(500)})))

    sent = json.loads(_sent(node)["result_preview"])
    assert len(sent["sq1"]["rows"]) == 20
    assert sent["sq1"]["row_count"] == 500
    assert sent["sq1"]["partial"] is True
    assert "t20" not in _sent(node)["result_preview"]


def test_the_preview_size_is_a_setting():
    node = _node(preview_rows=3)

    node(GraphState(user_query="q", aggregator_response=AggregatorResponse(terminal_results={"sq1": _rows(10)})))

    assert len(json.loads(_sent(node)["result_preview"])["sq1"]["rows"]) == 3


def test_the_settings_defaults():
    assert ResultSettings().preview_rows == 20
    assert ResultSettings().row_limit == 100_000


def test_the_prompt_asks_for_no_copy_of_the_result():
    prompt = ANSWER_SYNTHESIZER_PROMPT
    assert "Markdown table" not in prompt
    assert "{result_preview}" in prompt and "{aggregated_result}" not in prompt
    assert "row_count" in prompt and "partial" in prompt
    assert "in the table" in prompt


def test_the_schema_no_longer_asks_for_a_markdown_table():
    schema = json.dumps(AggregatedResponse.model_json_schema())
    assert "Markdown table" not in schema
    # Recorded demo answers still validate.
    AggregatedResponse(summary="s", format_type="table", content="| a |")


# --- the ceiling -----------------------------------------------------------

def _generate(adapter_row_limit, plan_limit=None, row_limit=100_000):
    adapter = SimpleNamespace(row_limit=adapter_row_limit, max_bytes=1000, get_dialect=lambda: "sqlite")
    node = GeneratorNode(fake_runtime(ds_registry=SimpleNamespace(get_adapter=lambda _id: adapter),
                                      settings={"results": {"row_limit": row_limit}}))
    plan = PlanModel(tables=[TableRef(name="users", alias="u")],
                     select_items=[SelectItem(expr=Expr(kind="column", alias="u", column_name="id"))],
                     joins=[], limit=plan_limit)
    state = SubgraphExecutionState(trace_id="t", sub_query=SubQuery(id="sq1", datasource_id="ds1", intent="q"),
                                   ast_planner_response=ASTPlannerResponse(plan=plan))
    return node(state)["generator_response"]


def test_a_datasource_with_no_row_limit_is_capped_at_the_ceiling():
    response = _generate(None, row_limit=100_000)

    assert response.row_cap == 100_000
    assert "LIMIT 100000" in response.sql_draft


def test_a_datasource_row_limit_below_the_ceiling_is_kept():
    assert _generate(10_000, row_limit=100_000).row_cap == 10_000


def test_the_ceiling_bounds_a_higher_datasource_row_limit():
    assert _generate(10_000, row_limit=50).row_cap == 50


def test_the_hosted_demo_datasources_are_capped_at_ten_thousand_rows():
    from nl2sql.datasets.demo import DEMO_DATASOURCES

    assert DEMO_DATASOURCES
    assert {ds["limits"]["row_limit"] for ds in DEMO_DATASOURCES} == {10_000}


# --- the truncated flag ----------------------------------------------------

def _artifact(row_count):
    return ArtifactRef(uri="u", format="parquet", row_count=row_count, columns=["c"],
                       bytes=1, content_hash="h", created_at=datetime(2026, 1, 1), path_template="p")


def _wrapped_output(row_cap, row_count):
    class _Sub:
        def invoke(self, state, config=None):
            return {**state,
                    "generator_response": GeneratorResponse(sql_draft="SELECT 1", row_cap=row_cap),
                    "executor_response": ExecutorResponse(executor_name="sql", subgraph_name="sql_agent",
                                                          node_id="sq1", trace_id="t1", tenant_id="t",
                                                          artifact=_artifact(row_count))}

    wrapped = wrap_subgraph(_Sub(), "sql_agent", SimpleNamespace(), execute=True)
    out = wrapped({"trace_id": "t1", "subgraph_id": "sql_agent:sq1:t1", "user_context": None,
                   "decomposer_response": DecomposerResponse(
                       sub_queries=[SubQuery(id="sq1", datasource_id="ds", intent="q")], combine_groups=[])})
    return out["subgraph_outputs"]["sql_agent:sq1:t1"]


def test_a_result_that_reaches_the_ceiling_is_flagged_truncated():
    assert _wrapped_output(row_cap=5, row_count=5).truncated is True


def test_a_result_under_the_ceiling_is_not_truncated():
    assert _wrapped_output(row_cap=5, row_count=4).truncated is False


def test_a_plan_s_own_top_n_is_never_truncated():
    assert _wrapped_output(row_cap=None, row_count=5).truncated is False


def _truncated_output():
    return SubgraphOutput(subgraph_id="sg", sub_query=SubQuery(id="sq1", datasource_id="ds", intent="q"),
                          row_cap=50, artifact=_artifact(50), truncated=True)


def test_the_query_result_carries_the_truncated_flag():
    result = result_from_state({"subgraph_outputs": {"sg": _truncated_output()}})

    [sub] = result.sub_queries
    assert sub.truncated is True and sub.row_cap == 50
    assert result.truncated is True


def test_a_complete_result_is_not_flagged():
    output = SubgraphOutput(subgraph_id="sg", sub_query=SubQuery(id="sq1", datasource_id="ds", intent="q"),
                            artifact=_artifact(3))
    result = result_from_state({"subgraph_outputs": {"sg": output}})

    assert result.sub_queries[0].truncated is False
    assert result.truncated is False


def test_the_answer_says_a_truncated_result_was_cut_and_to_narrow_the_question():
    node = _node()
    state = GraphState(user_query="q", aggregator_response=AggregatorResponse(terminal_results={"sq1": _rows(50)}),
                       subgraph_outputs={"sg": _truncated_output()})

    final = node(state)["answer_synthesizer_response"].final_answer

    note = _sent(node)["result_limit"]
    assert "cut at 50 rows" in note
    [warning] = final["warnings"]
    assert "cut at 50 rows" in warning and "narrow" in warning.lower()
    assert final["truncated"] is True


def test_a_complete_answer_is_not_flagged_and_says_nothing_about_a_limit():
    node = _node()

    final = node(GraphState(user_query="q", aggregator_response=AggregatorResponse(
        terminal_results={"sq1": _rows(50)})))["answer_synthesizer_response"].final_answer

    assert final["warnings"] == []
    assert final["truncated"] is False
    assert _sent(node)["result_limit"] == "None"
