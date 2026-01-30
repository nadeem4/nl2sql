from __future__ import annotations

import shutil
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from nl2sql.common.settings import settings
from nl2sql.context import NL2SQLContext
from nl2sql.pipeline.nodes.aggregator.node import EngineAggregatorNode
from nl2sql.pipeline.nodes.global_planner.schemas import (
    ExecutionDAG,
    LogicalNode,
    LogicalEdge,
    RelationSchema,
    ColumnSpec,
    GlobalPlannerResponse,
)
from nl2sql.pipeline.state import GraphState
from nl2sql.execution.artifacts.base import ArtifactStoreConfig
from nl2sql.execution.artifacts.local_store import LocalArtifactStore
from nl2sql_adapter_sdk.contracts import ResultFrame
from nl2sql.common.errors import ErrorSeverity


def _project_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _write_empty_secrets(tmp_path: Path) -> Path:
    secrets_path = tmp_path / "secrets.yaml"
    secrets_path.write_text("version: 1\nproviders: []\n", encoding="utf-8")
    return secrets_path


def _demo_config_paths(root: Path, secrets_config_path: Path) -> dict[str, Path]:
    return {
        "ds_config_path": root / "configs" / "datasources.demo.yaml",
        "llm_config_path": root / "configs" / "llm.demo.yaml",
        "policies_config_path": root / "configs" / "policies.demo.json",
        "secrets_config_path": secrets_config_path,
    }


def _demo_db_paths(root: Path) -> list[Path]:
    config_path = root / "configs" / "datasources.demo.yaml"
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    db_paths = []
    for datasource in payload.get("datasources", []):
        connection = datasource.get("connection") or {}
        database = connection.get("database")
        if database:
            db_paths.append(root / database)
    return db_paths


def _skip_if_missing_demo_dbs(root: Path) -> None:
    missing = [str(path) for path in _demo_db_paths(root) if not path.exists()]
    if missing:
        pytest.skip(f"Missing demo databases: {', '.join(missing)}")


@pytest.fixture(scope="module")
def demo_env() -> SimpleNamespace:
    root = _project_root()
    _skip_if_missing_demo_dbs(root)

    tmp_dir = Path(tempfile.mkdtemp(prefix="aggregator_real_data_"))
    secrets_path = _write_empty_secrets(tmp_dir)

    monkeypatch = pytest.MonkeyPatch()
    collection_name = f"itest_aggregator_{uuid.uuid4().hex}"
    vector_store_path = tmp_dir / "chroma"
    schema_store_path = tmp_dir / "schema_store.db"

    monkeypatch.setattr(settings, "result_artifact_backend", "local")
    monkeypatch.setattr(settings, "result_artifact_base_uri", tmp_dir.as_posix())
    monkeypatch.setattr(
        settings,
        "result_artifact_path_template",
        "<tenant_id>/<request_id>/<subgraph_name>/<dag_node_id>/<schema_version>/part-00000.parquet",
    )

    monkeypatch.setattr(settings, "vector_store_collection_name", collection_name)
    monkeypatch.setattr(settings, "vector_store_path", str(vector_store_path))
    monkeypatch.setattr(settings, "schema_store_backend", "sqlite")
    monkeypatch.setattr(settings, "schema_store_path", str(schema_store_path))
    monkeypatch.setattr(settings, "schema_store_max_versions", 3)

    ctx = NL2SQLContext(
        **_demo_config_paths(root, secrets_path),
        vector_store_path=vector_store_path,
    )

    env = SimpleNamespace(ctx=ctx, root=root, tmp_dir=tmp_dir)
    try:
        yield env
    finally:
        monkeypatch.undo()
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _schema(columns):
    return RelationSchema(columns=[ColumnSpec(name=c) for c in columns])


def test_aggregator_real_data(demo_env) -> None:
    node = EngineAggregatorNode(demo_env.ctx)

    scan = LogicalNode(node_id="sq_1", kind="scan", inputs=[], output_schema=_schema(["value"]))
    post_filter = LogicalNode(
        node_id="op_filter",
        kind="post_filter",
        inputs=["sq_1"],
        output_schema=_schema(["value"]),
        attributes={"operation": "filter", "filters": [{"attribute": "value", "operator": ">", "value": 10}]},
    )
    dag = ExecutionDAG(
        nodes=[scan, post_filter],
        edges=[LogicalEdge(edge_id="edge_f", from_id="sq_1", to_id="op_filter")],
    )

    store = LocalArtifactStore(
        ArtifactStoreConfig(
            backend="local",
            base_uri=settings.result_artifact_base_uri,
            path_template=settings.result_artifact_path_template,
        )
    )
    artifact = store.write_result_frame(
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
        global_planner_response=GlobalPlannerResponse(execution_dag=dag),
        artifact_refs={"sq_1": artifact},
    )

    response = node(state)["aggregator_response"]

    rows = response.terminal_results["op_filter"]
    assert [r["value"] for r in rows] == [20]
    assert all(e.severity not in (ErrorSeverity.CRITICAL, ErrorSeverity.ERROR) for e in response.errors)
