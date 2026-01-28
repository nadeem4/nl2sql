import tempfile

from nl2sql.execution.artifacts.base import ArtifactStoreConfig
from nl2sql.execution.artifacts.local_store import LocalArtifactStore
from nl2sql_adapter_sdk.contracts import ResultFrame, ResultColumn


def test_local_artifact_store_roundtrip():
    with tempfile.TemporaryDirectory() as tmpdir:
        store = LocalArtifactStore(
            ArtifactStoreConfig(
                backend="local",
                base_uri=tmpdir,
                path_template="<tenant_id>/<request_id>/<subgraph_name>/<dag_node_id>/<schema_version>/part-00000.parquet",
            )
        )

        frame = ResultFrame.from_row_dicts(
            [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}],
            columns=["a", "b"],
            row_count=2,
            success=True,
        )

        artifact = store.write_result_frame(
            frame,
            {
                "tenant_id": "t1",
                "request_id": "r1",
                "subgraph_name": "sql_agent",
                "dag_node_id": "node_1",
                "schema_version": "v1",
            },
        )

        loaded = store.read_result_frame(artifact)
        assert loaded.to_row_dicts() == frame.to_row_dicts()
