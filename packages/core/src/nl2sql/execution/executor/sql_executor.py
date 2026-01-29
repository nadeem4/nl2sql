from __future__ import annotations

from typing import Optional, Dict, Any

from nl2sql.common.errors import PipelineError, ErrorSeverity, ErrorCode
from nl2sql.common.logger import get_logger
from nl2sql.common.contracts import ExecutionRequest, ExecutionResult
from nl2sql.common.sandbox import execute_in_sandbox, get_execution_pool
from nl2sql.common.resilience import DB_BREAKER
from nl2sql.common.settings import settings
from nl2sql.execution.contracts import ExecutorRequest, ExecutorBaseModel
from nl2sql.execution.artifacts import build_artifact_store
from nl2sql_adapter_sdk.contracts import ResultFrame, ResultError, ResultColumn
from nl2sql_adapter_sdk.capabilities import DatasourceCapability
from nl2sql.datasources import DatasourceRegistry

from .base import ExecutorService

logger = get_logger("sql_executor")


def _execute_in_process(request: ExecutionRequest) -> ExecutionResult:
    import time
    from nl2sql.datasources.discovery import discover_adapters

    start = time.perf_counter()
    available = discover_adapters()
    if request.engine_type not in available:
        return ExecutionResult(
            success=False,
            error=f"Unknown datasource engine type: {request.engine_type}",
        )

    try:
        adapter_cls = available[request.engine_type]
        timeout_ms = request.limits.get("timeout_ms")
        row_limit = request.limits.get("row_limit")
        max_bytes = request.limits.get("max_bytes")

        adapter = adapter_cls(
            datasource_id=request.datasource_id,
            datasource_engine_type=request.engine_type,
            connection_args=request.connection_args,
            statement_timeout_ms=timeout_ms,
            row_limit=row_limit,
            max_bytes=max_bytes,
        )

        from nl2sql_adapter_sdk.contracts import AdapterRequest

        adapter_request = AdapterRequest(
            plan_type="sql",
            payload={"sql": request.sql},
            limits=request.limits,
        )
        sdk_result = adapter.execute(adapter_request)

        if isinstance(sdk_result, ResultFrame):
            if not sdk_result.success:
                err_msg = sdk_result.error.safe_message if sdk_result.error else "Adapter execution failed."
                return ExecutionResult(success=False, error=err_msg)
            rows_as_dicts = sdk_result.to_row_dicts()
            columns = [col.name for col in sdk_result.columns]
            row_count = sdk_result.row_count or len(rows_as_dicts)
            bytes_returned = sdk_result.bytes or 0
        else:
            rows_as_dicts = [dict(zip(sdk_result.columns, row)) for row in sdk_result.rows]
            columns = sdk_result.columns
            row_count = sdk_result.row_count
            bytes_returned = sdk_result.bytes_returned

        duration = (time.perf_counter() - start) * 1000
        return ExecutionResult(
            success=True,
            data={
                "row_count": row_count,
                "rows": rows_as_dicts,
                "columns": columns,
                "bytes_returned": bytes_returned,
            },
            metrics={"execution_time_ms": duration},
        )
    except Exception as exc:
        return ExecutionResult(success=False, error=str(exc))


class SqlExecutorService(ExecutorService):
    def __init__(self, ds_registry: DatasourceRegistry):
        self.ds_registry = ds_registry
        self.artifact_store = build_artifact_store()

    def execute(self, request: ExecutorRequest) -> ExecutorBaseModel:
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
            return ExecutorBaseModel(
                executor_name="sql_executor",
                subgraph_name=request.subgraph_name,
                node_id=request.node_id,
                trace_id=request.trace_id,
                datasource_id=request.datasource_id,
                schema_version=request.schema_version,
                errors=errors,
            )

        if not request.datasource_id:
            errors.append(
                PipelineError(
                    node="sql_executor",
                    message="No datasource_id provided.",
                    severity=ErrorSeverity.ERROR,
                    error_code=ErrorCode.MISSING_DATASOURCE_ID,
                )
            )
            return ExecutorBaseModel(
                executor_name="sql_executor",
                subgraph_name=request.subgraph_name,
                node_id=request.node_id,
                trace_id=request.trace_id,
                datasource_id=request.datasource_id,
                schema_version=request.schema_version,
                errors=errors,
            )

        ds_id = request.datasource_id
        adapter = self.ds_registry.get_adapter(ds_id)
        caps = self.ds_registry.get_capabilities(ds_id)
        if DatasourceCapability.SUPPORTS_SQL.value not in caps:
            errors.append(
                PipelineError(
                    node="sql_executor",
                    message=f"Datasource '{ds_id}' does not support SQL execution.",
                    severity=ErrorSeverity.ERROR,
                    error_code=ErrorCode.INVALID_STATE,
                )
            )
            return ExecutorBaseModel(
                executor_name="sql_executor",
                subgraph_name=request.subgraph_name,
                node_id=request.node_id,
                trace_id=request.trace_id,
                datasource_id=request.datasource_id,
                schema_version=request.schema_version,
                errors=errors,
            )

        safeguard_timeout_ms = adapter.statement_timeout_ms or settings.default_statement_timeout_ms
        safeguard_row_limit = adapter.row_limit or settings.default_row_limit
        safeguard_max_bytes = adapter.max_bytes or settings.default_max_bytes

        request_contract = ExecutionRequest(
            mode="execute",
            datasource_id=ds_id,
            engine_type=adapter.datasource_engine_type,
            connection_args=adapter.connection_args,
            sql=request.sql,
            limits={
                "timeout_ms": safeguard_timeout_ms,
                "row_limit": safeguard_row_limit,
                "max_bytes": safeguard_max_bytes,
            },
        )

        pool = get_execution_pool()
        try:
            @DB_BREAKER
            def _execute_guarded():
                res = execute_in_sandbox(pool, _execute_in_process, request_contract)
                if not res.success:
                    if res.metrics.get("is_crash"):
                        raise RuntimeError(f"Sandbox Crash: {res.error}")
                    if "timed out" in str(res.error).lower():
                        raise TimeoutError(res.error)
                return res

            result_contract = _execute_guarded()
        except Exception as exc:
            error_code = ErrorCode.DB_EXECUTION_ERROR
            if isinstance(exc, TimeoutError):
                error_code = ErrorCode.EXECUTION_TIMEOUT
            error = PipelineError(
                node="sql_executor",
                message=f"Execution failed: {exc}",
                severity=ErrorSeverity.ERROR,
                error_code=error_code,
            )
            return ExecutorBaseModel(
                executor_name="sql_executor",
                subgraph_name=request.subgraph_name,
                node_id=request.node_id,
                trace_id=request.trace_id,
                datasource_id=request.datasource_id,
                schema_version=request.schema_version,
                errors=[error],
            )

        if not result_contract.success:
            if result_contract.metrics.get("cancelled"):
                error = PipelineError(
                    node="sql_executor",
                    message="Execution cancelled by user.",
                    severity=ErrorSeverity.ERROR,
                    error_code=ErrorCode.CANCELLED,
                )
                return ExecutorBaseModel(
                    executor_name="sql_executor",
                    subgraph_name=request.subgraph_name,
                    node_id=request.node_id,
                    trace_id=request.trace_id,
                    datasource_id=request.datasource_id,
                    schema_version=request.schema_version,
                    errors=[error],
                )
            error = PipelineError(
                node="sql_executor",
                message=f"Execution error: {result_contract.error}",
                severity=ErrorSeverity.ERROR,
                error_code=ErrorCode.DB_EXECUTION_ERROR,
            )
            return ExecutorBaseModel(
                executor_name="sql_executor",
                subgraph_name=request.subgraph_name,
                node_id=request.node_id,
                trace_id=request.trace_id,
                datasource_id=request.datasource_id,
                schema_version=request.schema_version,
                errors=[error],
            )

        result_data = result_contract.data or {}
        rows = result_data.get("rows", [])
        columns = result_data.get("columns", [])
        row_count = result_data.get("row_count", len(rows))
        bytes_returned = result_data.get("bytes_returned", 0)

        if bytes_returned > safeguard_max_bytes:
            error = PipelineError(
                node="sql_executor",
                message=(
                    f"Result size ({bytes_returned} bytes) exceeds configured limit "
                    f"({safeguard_max_bytes} bytes)."
                ),
                severity=ErrorSeverity.ERROR,
                error_code=ErrorCode.SAFEGUARD_VIOLATION,
            )
            return ExecutorBaseModel(
                executor_name="sql_executor",
                subgraph_name=request.subgraph_name,
                node_id=request.node_id,
                trace_id=request.trace_id,
                datasource_id=request.datasource_id,
                schema_version=request.schema_version,
                errors=[error],
            )

        normalized_columns = []
        for col in columns:
            if isinstance(col, dict):
                normalized_columns.append(col.get("name"))
            elif hasattr(col, "name") and not isinstance(col, str):
                normalized_columns.append(getattr(col, "name"))
            else:
                normalized_columns.append(col)

        result_frame = ResultFrame.from_row_dicts(
            rows,
            columns=[ResultColumn(name=name, type="unknown") for name in normalized_columns],
            row_count=row_count,
            success=True,
            datasource_id=request.datasource_id,
            error=None,
        )

        tenant_id = request.user_context.tenant_id if request.user_context else "default"
        metadata = {
            "tenant_id": tenant_id or "default",
            "request_id": request.trace_id,
            "subgraph_name": request.subgraph_name,
            "dag_node_id": request.node_id,
            "schema_version": request.schema_version or "unknown",
        }
        artifact = self.artifact_store.write_result_frame(result_frame, metadata)

        exec_msg = f"Executed on {ds_id}. Rows: {row_count}."
        return ExecutorBaseModel(
            executor_name="sql_executor",
            subgraph_name=request.subgraph_name,
            node_id=request.node_id,
            trace_id=request.trace_id,
            datasource_id=request.datasource_id,
            schema_version=request.schema_version,
            artifact=artifact,
            metrics=result_contract.metrics or {},
            reasoning=[{"node": "sql_executor", "content": exec_msg}],
        )
