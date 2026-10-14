"""A result is fetched and written in batches, never held whole as Python rows.

An adapter that has ``execute_batches`` (``StreamingAdapterProtocol``) yields
the result a batch at a time and the executor writes each batch to the
sub-query's parquet artifact as it arrives. An adapter without it is called
through ``execute`` as before, so a third-party adapter keeps working.
Either way the artifact holds the same rows.
"""
from __future__ import annotations

from types import SimpleNamespace

import polars as pl
import pytest

from nl2sql.common.settings import settings
from nl2sql.execution.artifacts import ArtifactStore, ArtifactStoreConfig
from nl2sql.execution.contracts import ExecutorRequest
from nl2sql.execution.executor.sql_executor import SqlExecutorService
from nl2sql_sdk import StreamingAdapterProtocol
from nl2sql_sdk.contracts import ResultError, ResultFrame

COLUMNS = ["id", "name", "note"]


def _rows(n):
    # "note" is empty for the first rows, so a batch of them alone has no type.
    return [[i, f"name-{i}", None if i < 5 else f"note-{i}"] for i in range(n)]


class _OneShotAdapter:
    """A third-party adapter from before streaming: ``execute`` only."""

    def __init__(self, rows):
        self.rows = rows

    def execute(self, request):
        return ResultFrame(columns=COLUMNS, rows=self.rows, row_count=len(self.rows))


class _StreamingAdapter:
    def __init__(self, rows, fail_after=None):
        self.rows = rows
        self.fail_after = fail_after
        self.batch_sizes = []
        self.batches_yielded = 0

    def execute(self, request):
        raise AssertionError("a streaming adapter is read through execute_batches")

    def execute_batches(self, request, batch_size):
        self.batch_sizes.append(batch_size)
        for start in range(0, len(self.rows), batch_size):
            if self.fail_after is not None and self.batches_yielded == self.fail_after:
                yield ResultFrame(success=False, error=ResultError(
                    error_code="QUERY_REFUSED", safe_message="connection lost", retryable=False))
                return
            chunk = self.rows[start:start + batch_size]
            self.batches_yielded += 1
            yield ResultFrame(columns=COLUMNS, rows=chunk, row_count=len(chunk), bytes=10 * len(chunk))


def _service(tmp_path, monkeypatch, adapter):
    monkeypatch.setattr(settings, "result_artifact_backend", "local")
    monkeypatch.setattr(settings, "result_artifact_base_uri", str(tmp_path))
    monkeypatch.setattr(settings, "result_artifact_path_template", "<tenant_id>/<request_id>/<sub_query_id>.parquet")
    registry = SimpleNamespace(get_adapter=lambda ds: adapter, supports=lambda ds, cap: True)
    return SqlExecutorService(registry)


def _run(service):
    return service.execute(ExecutorRequest(node_id="sq1", trace_id="trace-1", subgraph_name="sql_agent",
                                           datasource_id="ds", sql="SELECT 1", tenant_id="t1"))


def _read(service, artifact):
    return service.artifact_store.read_parquet(artifact)


def test_the_streaming_protocol_is_part_of_the_sdk():
    assert isinstance(_StreamingAdapter([]), StreamingAdapterProtocol)
    assert not isinstance(_OneShotAdapter([]), StreamingAdapterProtocol)


def test_a_result_larger_than_a_batch_is_written_batch_by_batch(tmp_path, monkeypatch):
    adapter = _StreamingAdapter(_rows(23))
    service = _service(tmp_path / "streamed", monkeypatch, adapter)
    monkeypatch.setattr(settings, "result_fetch_batch_size", 5)

    response = _run(service)

    assert not response.errors
    assert adapter.batch_sizes == [5]
    assert adapter.batches_yielded == 5
    assert response.artifact.row_count == 23
    assert response.artifact.columns == COLUMNS
    assert response.metrics["row_count"] == 23
    assert response.metrics["bytes_returned"] == 230

    one_shot = _service(tmp_path / "one_shot", monkeypatch, _OneShotAdapter(_rows(23)))
    expected = _read(one_shot, _run(one_shot).artifact)
    assert _read(service, response.artifact).equals(expected)
    assert _read(service, response.artifact)["note"].dtype == pl.String


def test_an_adapter_without_execute_batches_still_works(tmp_path, monkeypatch):
    service = _service(tmp_path, monkeypatch, _OneShotAdapter(_rows(7)))

    response = _run(service)

    assert not response.errors
    assert response.artifact.row_count == 7
    assert _read(service, response.artifact)["id"].to_list() == list(range(7))


def test_a_failure_mid_stream_fails_the_sub_query_and_leaves_no_artifact(tmp_path, monkeypatch):
    service = _service(tmp_path, monkeypatch, _StreamingAdapter(_rows(23), fail_after=2))
    monkeypatch.setattr(settings, "result_fetch_batch_size", 5)

    response = _run(service)

    assert response.artifact is None
    assert [e.message for e in response.errors] == ["connection lost"]
    assert list(tmp_path.rglob("*.parquet")) == []


def test_an_empty_streamed_result_keeps_its_columns(tmp_path, monkeypatch):
    class _Empty(_StreamingAdapter):
        def execute_batches(self, request, batch_size):
            yield ResultFrame(columns=COLUMNS, rows=[], row_count=0)

    service = _service(tmp_path, monkeypatch, _Empty([]))

    response = _run(service)

    assert response.artifact.row_count == 0
    assert _read(service, response.artifact).columns == COLUMNS


def test_the_batch_size_has_a_setting():
    assert settings.result_fetch_batch_size == 10_000


def test_the_store_writes_batches_to_one_artifact(tmp_path):
    store = ArtifactStore(ArtifactStoreConfig(backend="local", base_uri=str(tmp_path),
                                              path_template="<tenant_id>/<request_id>/<sub_query_id>.parquet"))
    frames = [ResultFrame(columns=COLUMNS, rows=chunk, row_count=len(chunk))
              for chunk in (_rows(12)[:4], _rows(12)[4:8], _rows(12)[8:])]

    artifact = store.write_batches(frames, {"tenant_id": "t1", "request_id": "r1", "sub_query_id": "sq1"})

    assert artifact.row_count == 12
    assert artifact.uri.replace("\\", "/").endswith("t1/r1/sq1.parquet")
    assert store.read_parquet(artifact)["id"].to_list() == list(range(12))
    # Nothing but the artifact is left behind.
    assert [p.name for p in tmp_path.rglob("*") if p.is_file()] == ["sq1.parquet"]
