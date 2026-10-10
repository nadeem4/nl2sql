"""A combine input cut short by the adapter's row cap is caught, not combined.

The generator caps every sub-query at the adapter's row limit (1000 by
default) before the combine runs. A join whose input lost rows to that cap
silently drops every match those rows held, so the answer can be wrong with no
sign of it. The aggregator now refuses a ``join``/``compare`` with such an
input (``RESULT_TRUNCATED``), and carries a warning to the result for a
``union``, whose rows are still right, only incomplete.
"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from langgraph.graph import END

from nl2sql.common.errors import ErrorCode
from nl2sql.common.settings import settings
from nl2sql.execution.artifacts import ArtifactStore, ArtifactStoreConfig
from nl2sql.execution.contracts import ArtifactRef, ExecutorResponse
from nl2sql.execution.dag import ExecutionDAG, LogicalEdge, LogicalNode
from nl2sql.pipeline.graph_utils import wrap_subgraph
from nl2sql.pipeline.nodes.aggregator.node import EngineAggregatorNode
from nl2sql.pipeline.nodes.ast_planner.schemas import ASTPlannerResponse, Expr, PlanModel, SelectItem, TableRef
from nl2sql.pipeline.nodes.decomposer.schemas import DecomposerResponse, SubQuery
from nl2sql.pipeline.nodes.generator.node import GeneratorNode
from nl2sql.pipeline.nodes.generator.schemas import GeneratorResponse
from nl2sql.pipeline.routes import aggregator_route
from nl2sql.pipeline.state import GraphState, SubgraphExecutionState
from nl2sql.pipeline.subgraphs.schemas import SubgraphOutput
from nl2sql_adapter_sdk.contracts import ResultFrame


# --- the generator says when the cap, not the plan, bounds the query -------

def _generate(plan_limit):
    adapter = SimpleNamespace(row_limit=5, max_bytes=1000, get_dialect=lambda: "sqlite")
    node = GeneratorNode(SimpleNamespace(ds_registry=SimpleNamespace(get_adapter=lambda _id: adapter)))
    plan = PlanModel(tables=[TableRef(name="users", alias="u")],
                     select_items=[SelectItem(expr=Expr(kind="column", alias="u", column_name="id"))],
                     joins=[], limit=plan_limit)
    state = SubgraphExecutionState(trace_id="t", sub_query=SubQuery(id="sq1", datasource_id="ds1", intent="q"),
                                   ast_planner_response=ASTPlannerResponse(plan=plan))
    return node(state)["generator_response"]


def test_an_unlimited_plan_is_bounded_by_the_row_cap():
    assert _generate(None).row_cap == 5


def test_a_plan_asking_for_more_than_the_cap_is_bounded_by_it():
    assert _generate(100).row_cap == 5


def test_a_plan_s_own_top_n_is_not_a_cap():
    assert _generate(3).row_cap is None


# --- the subgraph output carries it to the aggregator ----------------------

def _artifact(row_count):
    return ArtifactRef(uri="u", backend="local", format="parquet", row_count=row_count, columns=["c"],
                       bytes=1, content_hash="h", created_at=datetime(2026, 1, 1), path_template="p")


def test_the_subgraph_output_records_the_row_cap():
    class _Sub:
        def invoke(self, state, config=None):
            return {**state,
                    "generator_response": GeneratorResponse(sql_draft="SELECT 1", row_cap=5),
                    "executor_response": ExecutorResponse(executor_name="sql", subgraph_name="sql_agent",
                                                          node_id="sq1", trace_id="t1", tenant_id="t",
                                                          artifact=_artifact(5))}

    wrapped = wrap_subgraph(_Sub(), "sql_agent", SimpleNamespace(), execute=True)
    out = wrapped({"trace_id": "t1", "subgraph_id": "sql_agent:sq1:t1", "user_context": None,
                   "decomposer_response": DecomposerResponse(
                       sub_queries=[SubQuery(id="sq1", datasource_id="ds", intent="q")], combine_groups=[])})

    assert out["subgraph_outputs"]["sql_agent:sq1:t1"].row_cap == 5


# --- the aggregator refuses a join on a capped input, warns on a union ------

def _combine_state(tmp_path, monkeypatch, operation, rows_left, rows_right, cap=3):
    monkeypatch.setattr(settings, "result_artifact_backend", "local")
    monkeypatch.setattr(settings, "result_artifact_base_uri", str(tmp_path))
    store = ArtifactStore(ArtifactStoreConfig(backend="local", base_uri=str(tmp_path),
                                              path_template="<request_id>/<sub_query_id>.parquet"))

    def scan(sq_id, n):
        frame = ResultFrame.from_row_dicts([{"customer_id": i, f"v_{sq_id}": i} for i in range(n)])
        return store.create_artifact_ref(frame, {"request_id": "r1", "sub_query_id": sq_id})

    refs = {"sq_a": scan("sq_a", rows_left), "sq_b": scan("sq_b", rows_right)}
    combine = LogicalNode(node_id="combine_g1", kind="combine", inputs=["sq_a", "sq_b"], attributes={
        "operation": operation, "group_id": "g1",
        "join_keys": [{"left": "customer_id", "right": "customer_id"}] if operation != "union" else []})
    dag = ExecutionDAG(
        nodes=[LogicalNode(node_id="sq_a", kind="scan", inputs=[]), LogicalNode(node_id="sq_b", kind="scan", inputs=[]),
               combine],
        edges=[LogicalEdge(edge_id="e1", from_id="sq_a", to_id="combine_g1", role="left"),
               LogicalEdge(edge_id="e2", from_id="sq_b", to_id="combine_g1", role="right")])
    outputs = {f"sql_agent:{sq}:r1": SubgraphOutput(subgraph_id=f"sql_agent:{sq}:r1",
                                                    sub_query=SubQuery(id=sq, datasource_id="ds", intent=sq),
                                                    artifact=refs[sq], row_cap=cap)
               for sq in ("sq_a", "sq_b")}
    return GraphState(user_query="q", execution_dag=dag, artifact_refs=refs, subgraph_outputs=outputs)


def test_a_join_with_an_input_cut_short_by_the_cap_is_refused(tmp_path, monkeypatch):
    state = _combine_state(tmp_path, monkeypatch, "join", rows_left=3, rows_right=2)

    out = EngineAggregatorNode(SimpleNamespace())(state)

    [error] = out["errors"]
    assert error.error_code == ErrorCode.RESULT_TRUNCATED
    assert "sq_a" in error.message and "3" in error.message and "join" in error.message
    assert out["aggregator_response"].terminal_results == {}
    assert aggregator_route(SimpleNamespace(aggregator_response=out["aggregator_response"])) == END


def test_a_compare_with_a_capped_input_is_refused_too(tmp_path, monkeypatch):
    state = _combine_state(tmp_path, monkeypatch, "compare", rows_left=2, rows_right=3)

    out = EngineAggregatorNode(SimpleNamespace())(state)

    assert [e.error_code for e in out["errors"]] == [ErrorCode.RESULT_TRUNCATED]


def test_a_union_with_a_capped_input_runs_and_warns(tmp_path, monkeypatch):
    state = _combine_state(tmp_path, monkeypatch, "union", rows_left=3, rows_right=2)
    # A union needs matching columns.
    state.artifact_refs["sq_b"] = state.artifact_refs["sq_a"]

    out = EngineAggregatorNode(SimpleNamespace())(state)

    assert not out.get("errors")
    assert len(out["aggregator_response"].terminal_results["combine_g1"]) == 6
    [warning] = out["warnings"]
    assert warning["error_code"] == ErrorCode.RESULT_TRUNCATED.value
    assert warning["sub_query_id"] == "sq_a"


def test_inputs_under_the_cap_join_as_before(tmp_path, monkeypatch):
    state = _combine_state(tmp_path, monkeypatch, "join", rows_left=2, rows_right=2)

    out = EngineAggregatorNode(SimpleNamespace())(state)

    assert not out.get("errors") and not out.get("warnings")
    assert len(out["aggregator_response"].terminal_results["combine_g1"]) == 2
