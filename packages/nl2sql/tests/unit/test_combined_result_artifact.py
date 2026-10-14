"""The combined result is saved as its own artifact, and the answer previews it.

The aggregator reads each sub-query's artifact lazily (a polars scan), writes
every terminal result of the DAG to a parquet artifact in the run's request
folder, and records it in ``AggregatorResponse.computed_artifacts`` with its
row count. ``terminal_results`` carries only the first ``ANSWER_PREVIEW_ROWS``
rows of it: what the answer model is shown, never the whole result.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import polars as pl
import pytest

from nl2sql.aggregation.engines.polars_duckdb import PolarsDuckdbEngine
from nl2sql.execution.artifacts import ArtifactStore, ArtifactStoreConfig
from nl2sql.execution.contracts import ArtifactRef
from nl2sql.execution.dag import ExecutionDAG, LogicalEdge, LogicalNode
from nl2sql.pipeline.nodes.aggregator.node import EngineAggregatorNode
from nl2sql.pipeline.nodes.aggregator.schemas import AggregatorResponse
from nl2sql.pipeline.nodes.answer_synthesizer.node import AnswerSynthesizerNode
from nl2sql.pipeline.state import GraphState
from nl2sql.testing.runtime import fake_runtime
from nl2sql_sdk.contracts import ResultFrame

TEMPLATE = "<tenant_id>/<request_id>/<sub_query_id>.parquet"


@pytest.fixture()
def store(tmp_path):
    return ArtifactStore(ArtifactStoreConfig(base_uri=str(tmp_path), path_template=TEMPLATE))


def _scan(store, sub_query_id, rows):
    return store.create_artifact_ref(
        ResultFrame.from_row_dicts(rows),
        {"tenant_id": "t1", "request_id": "run-1", "sub_query_id": sub_query_id},
    )


def _standalone_dag():
    return ExecutionDAG(
        nodes=[LogicalNode(node_id="sq1", kind="scan", inputs=[]),
               LogicalNode(node_id="combine_g1", kind="combine", inputs=["sq1"],
                           attributes={"operation": "standalone", "join_keys": []})],
        edges=[LogicalEdge(edge_id="e1", from_id="sq1", to_id="combine_g1")],
    )


def _aggregate(store, dag, refs, preview_rows=20):
    node = EngineAggregatorNode(fake_runtime(artifact_store=store, settings={
        "tenant_id": "t1", "results": {"preview_rows": preview_rows}}))
    return node(GraphState(trace_id="run-1", user_query="q", execution_dag=dag, artifact_refs=refs))


def test_the_combined_result_is_saved_in_the_request_folder(store, tmp_path):
    rows = [{"id": i, "name": f"n{i}"} for i in range(45)]
    refs = {"sq1": _scan(store, "sq1", rows)}

    response = _aggregate(store, _standalone_dag(), refs)["aggregator_response"]

    saved = response.computed_artifacts["combine_g1"]
    assert isinstance(saved, ArtifactRef)
    assert saved.row_count == 45
    assert saved.columns == ["id", "name"]
    assert Path(saved.uri).parent == Path(refs["sq1"].uri).parent == tmp_path / "t1" / "run-1"
    assert Path(saved.uri) != Path(refs["sq1"].uri)
    assert store.read_parquet(saved).to_dicts() == rows


def test_terminal_results_hold_only_the_answer_preview(store):
    refs = {"sq1": _scan(store, "sq1", [{"id": i} for i in range(45)])}

    response = _aggregate(store, _standalone_dag(), refs, preview_rows=20)["aggregator_response"]

    assert response.terminal_results["combine_g1"] == [{"id": i} for i in range(20)]


def test_a_union_of_two_scans_is_saved_whole(store):
    refs = {"sq1": _scan(store, "sq1", [{"id": i} for i in range(30)]),
            "sq2": _scan(store, "sq2", [{"id": i} for i in range(30, 50)])}
    dag = ExecutionDAG(
        nodes=[LogicalNode(node_id="sq1", kind="scan", inputs=[]),
               LogicalNode(node_id="sq2", kind="scan", inputs=[]),
               LogicalNode(node_id="combine_g1", kind="combine", inputs=["sq1", "sq2"],
                           attributes={"operation": "union", "join_keys": []})],
        edges=[LogicalEdge(edge_id="e1", from_id="sq1", to_id="combine_g1"),
               LogicalEdge(edge_id="e2", from_id="sq2", to_id="combine_g1")],
    )

    response = _aggregate(store, dag, refs)["aggregator_response"]

    saved = response.computed_artifacts["combine_g1"]
    assert saved.row_count == 50
    assert sorted(store.read_parquet(saved)["id"].to_list()) == list(range(50))


def test_scans_are_read_lazily(store):
    artifact = _scan(store, "sq1", [{"id": 1}])

    assert isinstance(PolarsDuckdbEngine(store).load_scan(artifact), pl.LazyFrame)


def test_the_answer_prompt_counts_the_saved_result_not_the_preview(monkeypatch):
    seen = {}

    class _Chain:
        def invoke(self, values):
            seen.update(values)
            from nl2sql.pipeline.nodes.answer_synthesizer.schemas import AggregatedResponse
            return AggregatedResponse(summary="s", format_type="text", content="c", warnings=[])

    node = AnswerSynthesizerNode.__new__(AnswerSynthesizerNode)
    node.node_name = "answer_synthesizer"
    node.preview_rows = 20
    node.chain = _Chain()
    saved = ArtifactRef(uri="u", format="parquet", row_count=45, columns=["id"], bytes=1,
                        content_hash="h", created_at="2026-01-01T00:00:00", path_template=TEMPLATE)
    response = AggregatorResponse(terminal_results={"combine_g1": [{"id": i} for i in range(20)]},
                                  computed_artifacts={"combine_g1": saved})

    node(GraphState(user_query="q", aggregator_response=response))

    assert '"row_count": 45' in seen["result_preview"]
    assert '"partial": true' in seen["result_preview"]
