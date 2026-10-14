from __future__ import annotations

import shutil
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from nl2sql.runtime import Runtime
from nl2sql.testing.config import make_demo_config
from nl2sql.pipeline.nodes.aggregator.node import EngineAggregatorNode
from nl2sql.pipeline.nodes.answer_synthesizer.node import AnswerSynthesizerNode
from nl2sql.execution.dag import (
    ExecutionDAG,
    LogicalNode,
    LogicalEdge,
)
from nl2sql.pipeline.nodes.aggregator.schemas import AggregatorResponse
from nl2sql.pipeline.state import GraphState
from nl2sql.execution.artifacts import ArtifactStore, ArtifactStoreConfig
from nl2sql_sdk.contracts import ResultFrame


# The node under test builds an LLM client in its constructor, so these cannot
# run without a real key. See docs/testing/architecture.md.
pytestmark = pytest.mark.llm


def _project_root() -> Path:
    return Path(__file__).resolve().parents[4]


@pytest.fixture(scope="module")
def demo_env() -> SimpleNamespace:
    root = _project_root()

    tmp_dir = Path(tempfile.mkdtemp(prefix="answer_synthesizer_real_data_"))

    monkeypatch = pytest.MonkeyPatch()
    collection_name = f"itest_answer_synth_{uuid.uuid4().hex}"
    vector_store_path = tmp_dir / "chroma"
    schema_store_path = tmp_dir / "schema_store.db"

    ctx = Runtime.from_config(make_demo_config(tmp_dir))

    env = SimpleNamespace(ctx=ctx, root=root, tmp_dir=tmp_dir)
    try:
        yield env
    finally:
        monkeypatch.undo()
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _build_aggregator_response(ctx) -> AggregatorResponse:
    scan = LogicalNode(node_id="sq_1", kind="scan", inputs=[])
    post_filter = LogicalNode(
        node_id="op_filter",
        kind="post_filter",
        inputs=["sq_1"],
        attributes={"operation": "filter", "filters": [{"attribute": "value", "operator": ">", "value": 10}]},
    )
    dag = ExecutionDAG(
        nodes=[scan, post_filter],
        edges=[LogicalEdge(edge_id="edge_f", from_id="sq_1", to_id="op_filter")],
    )

    store = ArtifactStore(
        ArtifactStoreConfig(
            base_uri=ctx.artifact_store.config.base_uri,
            path_template="<tenant_id>/<request_id>/<subgraph_name>/<dag_node_id>/<schema_version>/part-00000.parquet",
        )
    )
    artifact = store.create_artifact_ref(
        ResultFrame.from_row_dicts([{"value": 5}, {"value": 20}]),
        {
            "tenant_id": "t1",
            "request_id": "r1",
            "subgraph_name": "sql_agent",
            "dag_node_id": "sq_1",
            "schema_version": "v1",
        },
    )
    state = GraphState(
        user_query="q",
        execution_dag=dag,
        artifact_refs={"sq_1": artifact},
    )
    return EngineAggregatorNode(ctx)(state)["aggregator_response"]


def test_answer_synthesizer_real_data(demo_env) -> None:
    node = AnswerSynthesizerNode(demo_env.ctx)
    aggregated = _build_aggregator_response(demo_env.ctx)
    state = GraphState(
        user_query="Summarize results",
        aggregator_response=aggregated,
    )

    result = node(state)

    final = result["answer_synthesizer_response"].final_answer
    assert final["summary"]
    assert final["format_type"] in {"table", "list", "text"}
    assert final["content"]
