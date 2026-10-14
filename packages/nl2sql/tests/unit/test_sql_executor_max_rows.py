"""``ExecutorRequest.max_rows`` and ``parameters``: the executor's side of verified SQL.

A verified query is a person's SQL, so the row ceiling is not added to its text
as a ``LIMIT``: the executor stops reading the result at ``max_rows``. Its
parameter values travel to the adapter in ``AdapterRequest.parameters``.
Generated SQL sets neither, and is read exactly as before.
"""
from __future__ import annotations

from types import SimpleNamespace

from nl2sql.execution.artifacts import ArtifactStore, ArtifactStoreConfig
from nl2sql.execution.contracts import ExecutorRequest
from nl2sql.execution.executor.sql_executor import SqlExecutorService
from nl2sql_sdk.contracts import ResultFrame

COLUMNS = ["id"]


class _StreamingAdapter:
    def __init__(self, n):
        self.n = n
        self.requests = []
        self.closed = False
        self.batches_yielded = 0

    def execute(self, request):
        raise AssertionError("read through execute_batches")

    def execute_batches(self, request, batch_size):
        self.requests.append(request)
        try:
            for start in range(0, self.n, batch_size):
                chunk = [[i] for i in range(start, min(start + batch_size, self.n))]
                self.batches_yielded += 1
                yield ResultFrame(columns=COLUMNS, rows=chunk, row_count=len(chunk), bytes=len(chunk))
        finally:
            self.closed = True


class _OneShotAdapter:
    def __init__(self, n):
        self.n = n
        self.requests = []

    def execute(self, request):
        self.requests.append(request)
        rows = [[i] for i in range(self.n)]
        return ResultFrame(columns=COLUMNS, rows=rows, row_count=len(rows))


def _service(tmp_path, adapter, batch_size=4):
    store = ArtifactStore(ArtifactStoreConfig(base_uri=str(tmp_path),
                                              path_template="<tenant_id>/<request_id>/<sub_query_id>.parquet"))
    registry = SimpleNamespace(get_adapter=lambda ds: adapter, supports=lambda ds, cap: True)
    return SqlExecutorService(registry, store, fetch_batch_size=batch_size)


def _request(**extra):
    fields = {"node_id": "sq1", "trace_id": "trace-1", "subgraph_name": "curated_matcher",
              "datasource_id": "ds", "sql": "SELECT id FROM t", "tenant_id": "t1"}
    return ExecutorRequest(**{**fields, **extra})


def test_the_stream_stops_at_max_rows_and_the_rest_is_never_read(tmp_path):
    adapter = _StreamingAdapter(100)
    service = _service(tmp_path, adapter, batch_size=4)

    response = service.execute(_request(max_rows=10))

    assert response.errors == []
    assert response.artifact.row_count == 10
    assert response.metrics["row_count"] == 10
    rows = service.artifact_store.read_parquet(response.artifact)["id"].to_list()
    assert rows == list(range(10))
    # Three batches of four cover ten rows; the fourth is never asked for, and
    # the adapter's stream is closed (its connection released).
    assert adapter.batches_yielded == 3
    assert adapter.closed


def test_a_result_under_max_rows_is_whole(tmp_path):
    adapter = _StreamingAdapter(7)
    service = _service(tmp_path, adapter)

    response = service.execute(_request(max_rows=10))

    assert response.artifact.row_count == 7


def test_max_rows_applies_to_an_adapter_that_does_not_stream(tmp_path):
    adapter = _OneShotAdapter(25)
    service = _service(tmp_path, adapter)

    response = service.execute(_request(max_rows=10))

    assert response.artifact.row_count == 10


def test_without_max_rows_every_row_is_read(tmp_path):
    adapter = _StreamingAdapter(30)
    service = _service(tmp_path, adapter)

    response = service.execute(_request())

    assert response.artifact.row_count == 30


def test_parameters_reach_the_adapter_and_the_sql_text_is_untouched(tmp_path):
    adapter = _StreamingAdapter(3)
    service = _service(tmp_path, adapter)

    service.execute(_request(sql="SELECT id FROM t WHERE y = :year", parameters={"year": 2012}))

    [sent] = adapter.requests
    assert sent.parameters == {"year": 2012}
    assert sent.payload == {"sql": "SELECT id FROM t WHERE y = :year"}


def test_generated_sql_sends_no_parameters(tmp_path):
    adapter = _StreamingAdapter(3)
    service = _service(tmp_path, adapter)

    service.execute(_request())

    [sent] = adapter.requests
    assert sent.parameters == {}
