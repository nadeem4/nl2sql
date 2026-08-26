import tempfile
from pathlib import Path

import polars as pl
import pytest

from nl2sql.common.settings import settings
from nl2sql.execution.artifacts import ArtifactStore, ArtifactStoreConfig, build_artifact_store
from nl2sql_adapter_sdk.contracts import ResultFrame


def _frame():
    return ResultFrame.from_row_dicts([{"id": 1, "value": "a"}, {"id": 2, "value": "b"}])


def _metadata():
    return {"tenant_id": "t1", "request_id": "r1", "schema_version": "v1"}


def _local_config(base_uri, template="<tenant_id>/<request_id>.parquet"):
    return ArtifactStoreConfig(backend="local", base_uri=base_uri, path_template=template)


class TestLocalBackend:
    def test_round_trips_a_result_frame_through_real_io(self):
        # Validates the only backend reachable in CI because a lossy write would corrupt aggregation input.
        # Arrange
        with tempfile.TemporaryDirectory() as tmpdir:
            store = ArtifactStore(_local_config(tmpdir))
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
            store = ArtifactStore(_local_config(tmpdir, template))

            # Act
            artifact = store.create_artifact_ref(_frame(), _metadata())

            # Assert
            expected = Path(tmpdir).resolve() / "t1" / "r1" / "v1" / "part-00000.parquet"
            assert Path(artifact.uri) == expected
            assert expected.exists()
            assert artifact.bytes == expected.stat().st_size

    def test_reads_a_polars_frame_for_the_aggregation_engine(self):
        # Validates the DataFrame read path because the aggregator consumes polars directly.
        # Arrange
        with tempfile.TemporaryDirectory() as tmpdir:
            store = ArtifactStore(_local_config(tmpdir))

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
            store = ArtifactStore(_local_config(tmpdir, template))

            # Act / Assert
            with pytest.raises(ValueError, match="dag_node_id"):
                store.create_artifact_ref(_frame(), _metadata())

    def test_default_setting_is_renderable_from_executor_metadata(self):
        # Validates the shipped default because the executor supplies only these three keys.
        # Arrange
        with tempfile.TemporaryDirectory() as tmpdir:
            store = ArtifactStore(_local_config(tmpdir, settings.result_artifact_path_template))

            # Act
            artifact = store.create_artifact_ref(_frame(), _metadata())

            # Assert
            assert Path(artifact.uri) == Path(tmpdir).resolve() / "t1" / "r1.parquet"


class TestS3Backend:
    def test_builds_a_bucket_and_prefix_uri(self, monkeypatch):
        # Validates URI construction because this is exactly what was unreachable before.
        # Arrange
        written = {}
        monkeypatch.setattr(
            "nl2sql.execution.artifacts.store.write_parquet",
            lambda df, target, storage_options=None: written.update(
                target=target, storage_options=storage_options
            ),
        )
        store = ArtifactStore(
            ArtifactStoreConfig(
                backend="s3",
                base_uri="",
                path_template="<tenant_id>/<request_id>.parquet",
                s3_bucket="bucket",
                s3_prefix="prefix/",
            )
        )

        # Act
        artifact = store.create_artifact_ref(_frame(), _metadata())

        # Assert
        assert artifact.uri == "s3://bucket/prefix/t1/r1.parquet"
        assert written["target"] == "s3://bucket/prefix/t1/r1.parquet"
        assert written["storage_options"] is None

    def test_omits_an_unset_prefix(self, monkeypatch):
        # Validates the no-prefix case because a stray leading slash breaks S3 keys.
        # Arrange
        monkeypatch.setattr("nl2sql.execution.artifacts.store.write_parquet", lambda *a, **k: None)
        store = ArtifactStore(
            ArtifactStoreConfig(
                backend="s3",
                base_uri="",
                path_template="<tenant_id>/<request_id>.parquet",
                s3_bucket="bucket",
            )
        )

        # Act
        artifact = store.create_artifact_ref(_frame(), _metadata())

        # Assert
        assert artifact.uri == "s3://bucket/t1/r1.parquet"

    def test_raises_when_bucket_is_not_configured(self):
        # Validates the config guard because a missing bucket must not produce a malformed URI.
        # Arrange
        store = ArtifactStore(
            ArtifactStoreConfig(
                backend="s3", base_uri="", path_template="<tenant_id>/<request_id>.parquet"
            )
        )

        # Act / Assert
        with pytest.raises(ValueError, match="RESULT_ARTIFACT_S3_BUCKET"):
            store.create_artifact_ref(_frame(), _metadata())

    def test_reads_back_through_the_same_uri(self, monkeypatch):
        # Validates the read mirror because reads and writes must agree on storage options.
        # Arrange
        read = {}

        def _fake_read(source, storage_options=None):
            read.update(source=source, storage_options=storage_options)
            return pl.DataFrame({"id": [1]})

        monkeypatch.setattr("nl2sql.execution.artifacts.store.write_parquet", lambda *a, **k: None)
        monkeypatch.setattr("nl2sql.execution.artifacts.store.read_parquet", _fake_read)
        store = ArtifactStore(
            ArtifactStoreConfig(
                backend="s3",
                base_uri="",
                path_template="<tenant_id>/<request_id>.parquet",
                s3_bucket="bucket",
            )
        )
        artifact = store.create_artifact_ref(_frame(), _metadata())

        # Act
        frame = store.read_result_frame(artifact)

        # Assert
        assert read["source"] == "s3://bucket/t1/r1.parquet"
        assert read["storage_options"] is None
        assert frame.columns == ["id"]


class TestAdlsBackend:
    def test_builds_an_abfs_uri_and_passes_the_connection_string(self, monkeypatch):
        # Validates URI construction because this is exactly what was unreachable before.
        # Arrange
        written = {}
        monkeypatch.setattr(
            "nl2sql.execution.artifacts.store.write_parquet",
            lambda df, target, storage_options=None: written.update(
                target=target, storage_options=storage_options
            ),
        )
        store = ArtifactStore(
            ArtifactStoreConfig(
                backend="adls",
                base_uri="",
                path_template="<tenant_id>/<request_id>.parquet",
                adls_account="acct",
                adls_container="container",
                adls_connection_string="conn-str",
            )
        )

        # Act
        artifact = store.create_artifact_ref(_frame(), _metadata())

        # Assert
        assert artifact.uri == "abfs://container@acct.dfs.core.windows.net/t1/r1.parquet"
        assert written["target"] == artifact.uri
        assert written["storage_options"] == {"connection_string": "conn-str"}

    def test_omits_storage_options_without_a_connection_string(self, monkeypatch):
        # Validates credential-free config because storage options must not carry a None value.
        # Arrange
        written = {}
        monkeypatch.setattr(
            "nl2sql.execution.artifacts.store.write_parquet",
            lambda df, target, storage_options=None: written.update(storage_options=storage_options),
        )
        store = ArtifactStore(
            ArtifactStoreConfig(
                backend="adls",
                base_uri="",
                path_template="<tenant_id>/<request_id>.parquet",
                adls_account="acct",
                adls_container="container",
            )
        )

        # Act
        store.create_artifact_ref(_frame(), _metadata())

        # Assert
        assert written["storage_options"] is None

    def test_raises_when_container_is_not_configured(self):
        # Validates the config guard because abfs URIs are unbuildable without a container.
        # Arrange
        store = ArtifactStore(
            ArtifactStoreConfig(
                backend="adls",
                base_uri="",
                path_template="<tenant_id>/<request_id>.parquet",
                adls_account="acct",
            )
        )

        # Act / Assert
        with pytest.raises(ValueError, match="RESULT_ARTIFACT_ADLS_CONTAINER"):
            store.create_artifact_ref(_frame(), _metadata())

    def test_raises_when_account_is_not_configured(self):
        # Validates the config guard because abfs URIs are unbuildable without an account.
        # Arrange
        store = ArtifactStore(
            ArtifactStoreConfig(
                backend="adls",
                base_uri="",
                path_template="<tenant_id>/<request_id>.parquet",
                adls_container="container",
            )
        )

        # Act / Assert
        with pytest.raises(ValueError, match="RESULT_ARTIFACT_ADLS_ACCOUNT"):
            store.create_artifact_ref(_frame(), _metadata())


class TestFactory:
    @pytest.mark.parametrize("backend", ["local", "s3", "adls"])
    def test_every_configured_backend_can_be_constructed(self, backend, monkeypatch):
        # Validates importability because two backends shipped unimportable for want of this test.
        # Arrange
        monkeypatch.setattr(settings, "result_artifact_backend", backend)

        # Act
        store = build_artifact_store()

        # Assert
        assert store.config.backend == backend

    def test_rejects_an_unknown_backend(self, monkeypatch):
        # Validates the guard because a typo must not silently write to the wrong place.
        # Arrange
        monkeypatch.setattr(settings, "result_artifact_backend", "gcs")
        store = build_artifact_store()

        # Act / Assert
        with pytest.raises(ValueError, match="gcs"):
            store.create_artifact_ref(_frame(), _metadata())
