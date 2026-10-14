from __future__ import annotations

from typing import Any, Iterator, Optional

from nl2sql.common.errors import PipelineError, ErrorSeverity, ErrorCode
from nl2sql.common.logger import get_logger
from nl2sql.common.settings import settings
from nl2sql.execution.contracts import ExecutorRequest, ExecutorResponse
from nl2sql.execution.artifacts import build_artifact_store
from nl2sql_sdk.capabilities import DatasourceCapability
from nl2sql.datasources import DatasourceRegistry
from nl2sql_sdk.contracts import AdapterRequest, ResultFrame
from nl2sql_sdk.protocols import StreamingAdapterProtocol

logger = get_logger("sql_executor")


class _StreamFailed(Exception):
    """Raised into the artifact writer when the adapter reports a failure mid-result."""


def _frames(adapter: Any, request: AdapterRequest) -> Iterator[ResultFrame]:
    """The adapter's result, a batch at a time when it streams, else in one frame.

    ``execute_batches`` is the SDK's optional ``StreamingAdapterProtocol``; an
    adapter without it (any written before it) is called through ``execute``.
    """
    if isinstance(adapter, StreamingAdapterProtocol):
        yield from adapter.execute_batches(request, int(settings.result_fetch_batch_size))
    else:
        yield adapter.execute(request)


class _Stream:
    """Passes the adapter's frames to the writer, counting them and stopping at a failure.

    With ``max_rows`` it also stops once that many rows have passed: the last
    frame is cut to fit and the adapter's stream is closed, so the rest of the
    result is never fetched.
    """

    def __init__(self, frames: Iterator[ResultFrame], max_rows: Optional[int] = None):
        self._frames = frames
        self._max_rows = max_rows
        self.failure: Optional[ResultFrame] = None
        self.row_count = 0
        self.bytes = 0

    def __iter__(self) -> Iterator[ResultFrame]:
        try:
            for frame in self._frames:
                if not frame.success:
                    self.failure = frame
                    raise _StreamFailed()
                if self._max_rows is not None and self.row_count + frame.row_count > self._max_rows:
                    keep = self._max_rows - self.row_count
                    frame = frame.model_copy(update={"rows": frame.rows[:keep], "row_count": keep})
                self.row_count += frame.row_count
                self.bytes += frame.bytes or 0
                yield frame
                if self._max_rows is not None and self.row_count >= self._max_rows:
                    return
        finally:
            close = getattr(self._frames, "close", None)
            if close is not None:
                close()


class SqlExecutorService:
    def __init__(self, ds_registry: DatasourceRegistry):
        self.ds_registry = ds_registry
        self.artifact_store = build_artifact_store()

    def validate_request(self, request: ExecutorRequest) -> list[PipelineError]:
        errors = []
        if not request.sql:
            errors.append(
                PipelineError(
                    node="sql_executor",
                    message="No SQL to execute.",
                    severity=ErrorSeverity.ERROR,
                    error_code=ErrorCode.MISSING_SQL,
                )
            )

        if not request.datasource_id:
            errors.append(
                PipelineError(
                    node="sql_executor",
                    message="No datasource_id provided.",
                    severity=ErrorSeverity.ERROR,
                    error_code=ErrorCode.MISSING_DATASOURCE_ID
                )
            )

        ds_id = request.datasource_id
        if not self.ds_registry.supports(ds_id, DatasourceCapability.SUPPORTS_SQL):
            errors.append(
                PipelineError(
                    node="sql_executor",
                    message=f"Datasource '{ds_id}' does not support SQL execution.",
                    severity=ErrorSeverity.ERROR,
                    error_code=ErrorCode.INVALID_STATE,
                )
            )
        return errors

    def execute(self, request: ExecutorRequest) -> ExecutorResponse:
        errors = self.validate_request(request)
        if errors:
            return ExecutorResponse(
                executor_name="sql_executor",
                subgraph_name=request.subgraph_name,
                node_id=request.node_id,
                trace_id=request.trace_id,
                datasource_id=request.datasource_id,
                schema_version=request.schema_version,
                errors=errors,
                tenant_id=request.tenant_id,
            )
        
        ds_id = request.datasource_id
        adapter = self.ds_registry.get_adapter(ds_id)

        adapter_request = AdapterRequest(
            plan_type="sql",
            payload={"sql": request.sql},
            parameters=dict(request.parameters),
            limits={}
        )

        # request_id is the run's trace id, shared by all its sub-queries;
        # sub_query_id (the scan node's id) keeps their artifacts apart.
        metadata = {
            "schema_version": request.schema_version,
            "request_id": request.trace_id,
            "tenant_id": request.tenant_id,
            "sub_query_id": request.node_id,
            "dag_node_id": request.node_id,
            "subgraph_name": request.subgraph_name,
        }
        stream = _Stream(_frames(adapter, adapter_request), request.max_rows)
        try:
            artifact_ref = self.artifact_store.write_batches(stream, metadata)
        except _StreamFailed:
            artifact_ref = None

        if stream.failure is not None:
            # The adapter is the only one that knows its driver, so it says
            # whether the query itself was at fault (``retryable``): such an
            # error goes back to the planner; anything else ends the sub-query.
            adapter_error = stream.failure.error
            retryable = bool(adapter_error and adapter_error.retryable)
            error = PipelineError(
                node="sql_executor",
                message=adapter_error.safe_message if adapter_error else "SQL execution failed.",
                severity=ErrorSeverity.ERROR,
                error_code=ErrorCode.EXECUTION_FAILED if retryable else ErrorCode.DB_EXECUTION_ERROR,
                details={"adapter_error_code": adapter_error.error_code} if adapter_error else None,
            )
            return ExecutorResponse(
                executor_name="sql_executor",
                subgraph_name=request.subgraph_name,
                node_id=request.node_id,
                trace_id=request.trace_id,
                datasource_id=request.datasource_id,
                schema_version=request.schema_version,
                errors=[error],
                tenant_id=request.tenant_id,
            )

        return ExecutorResponse(
            executor_name="sql_executor",
            subgraph_name=request.subgraph_name,
            node_id=request.node_id,
            trace_id=request.trace_id,
            datasource_id=request.datasource_id,
            schema_version=request.schema_version,
            artifact=artifact_ref,
            metrics={
                "row_count": stream.row_count,
                "bytes_returned": stream.bytes,
            },
            tenant_id=request.tenant_id,
        )