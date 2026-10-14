import tempfile
from pathlib import Path

import polars as pl
import pytest

from nl2sql.execution.artifacts import ArtifactStore
from nl2sql.storage.local import LocalResults
from nl2sql.execution.artifacts.store import DEFAULT_PATH_TEMPLATE
from nl2sql_sdk.contracts import ResultFrame


def _frame():
    return ResultFrame.from_row_dicts([{"id": 1, "value": "a"}, {"id": 2, "value": "b"}])


def _metadata():
    return {"tenant_id": "t1", "request_id": "r1", "schema_version": "v1"}


def _executor_metadata():
    # What SqlExecutorService passes for one sub-query of a run.
    return {**_metadata(), "sub_query_id": "sq_1", "dag_node_id": "sq_1", "subgraph_name": "sql_agent"}


def _store(folder, template="<tenant_id>/<request_id>.parquet"):
    return ArtifactStore(LocalResults(folder), path_template=template)


class TestLocalBackend:
    def test_round_trips_a_result_frame_through_real_io(self):
        # Validates the only backend reachable in CI because a lossy write would corrupt aggregation input.
        # Arrange
        with tempfile.TemporaryDirectory() as tmpdir:
            store = _store(tmpdir)
            frame = _frame()

            # Act
            artifact = store.create_artifact_ref(frame, _metadata())
            restored = store.read_result_frame(artifact)

            # Assert
            assert restored.columns == frame.columns
            assert restored.to_row_dicts() == frame.to_row_dicts()
            assert restored.row_count == 2

    def test_writes_to_the_path_the_template_describes(self):
        # Validates template rendering because the artifact layout is a documented contract.
        # Arrange
        with tempfile.TemporaryDirectory() as tmpdir:
            template = "<tenant_id>/<request_id>/<schema_version>/part-00000.parquet"
            store = _store(tmpdir, template)

            # Act
            artifact = store.create_artifact_ref(_frame(), _metadata())

            # Assert
            expected = Path(tmpdir) / "t1" / "r1" / "v1" / "part-00000.parquet"
            assert artifact.uri == "t1/r1/v1/part-00000.parquet"
            assert expected.exists()
            assert artifact.bytes == expected.stat().st_size

    def test_reads_a_polars_frame_for_the_aggregation_engine(self):
        # Validates the DataFrame read path because the aggregator consumes polars directly.
        # Arrange
        with tempfile.TemporaryDirectory() as tmpdir:
            store = _store(tmpdir)

            # Act
            artifact = store.create_artifact_ref(_frame(), _metadata())
            df = store.read_parquet(artifact)

            # Assert
            assert isinstance(df, pl.DataFrame)
            assert df.columns == ["id", "value"]
            assert df.height == 2


class TestPathTemplate:
    def test_raises_naming_the_placeholder_it_cannot_fill(self):
        # Validates the failure mode because an unrendered placeholder on disk is worse than an error.
        # Arrange
        with tempfile.TemporaryDirectory() as tmpdir:
            template = "<tenant_id>/<request_id>/<dag_node_id>.parquet"
            store = _store(tmpdir, template)

            # Act / Assert
            with pytest.raises(ValueError, match="dag_node_id"):
                store.create_artifact_ref(_frame(), _metadata())

    def test_default_setting_is_renderable_from_executor_metadata(self):
        # Validates the shipped default against the keys the SQL executor supplies.
        # Arrange
        with tempfile.TemporaryDirectory() as tmpdir:
            store = _store(tmpdir, DEFAULT_PATH_TEMPLATE)

            # Act
            artifact = store.create_artifact_ref(_frame(), _executor_metadata())

            # Assert
            assert artifact.uri == "t1/r1/sq_1.parquet"
            assert (Path(tmpdir) / "t1" / "r1" / "sq_1.parquet").is_file()
