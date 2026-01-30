from __future__ import annotations

from typing import Optional, Dict, Any

from nl2sql.common.errors import PipelineError, ErrorSeverity, ErrorCode
from nl2sql.common.logger import get_logger
from nl2sql.common.contracts import ExecutionRequest, ExecutionResult
from nl2sql.common.sandbox import execute_in_sandbox, get_execution_pool
from nl2sql.common.resilience import DB_BREAKER
from nl2sql.common.settings import settings
from nl2sql.execution.contracts import ExecutorRequest, ExecutorResponse
from nl2sql.execution.artifacts import build_artifact_store
from nl2sql_adapter_sdk.contracts import ResultFrame
from nl2sql_adapter_sdk.capabilities import DatasourceCapability
from nl2sql.datasources import DatasourceRegistry
from nl2sql_adapter_sdk.contracts import AdapterRequest

from .base import ExecutorService

logger = get_logger("sql_executor")


class SqlExecutorService(ExecutorService):
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
            limits={}
        )

        result_frame = adapter.execute(adapter_request)

        if not result_frame.success:
            error_msg = result_frame.error.safe_message if result_frame.error else "SQL execution failed."
            error = PipelineError(
                node="sql_executor",
                message=error_msg,
                severity=ErrorSeverity.ERROR,
                error_code=ErrorCode.EXECUTION_FAILED,
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
    
        artifact_ref = self.artifact_store.create_artifact_ref(
            result_frame, {"schema_version": request.schema_version, "request_id": request.trace_id, "tenant_id": request.tenant_id} )
        
        return ExecutorResponse(
            executor_name="sql_executor",
            subgraph_name=request.subgraph_name,
            node_id=request.node_id,
            trace_id=request.trace_id,
            datasource_id=request.datasource_id,
            schema_version=request.schema_version,
            artifact=artifact_ref,
            metrics={
                "row_count": result_frame.row_count,
                "bytes_returned": result_frame.bytes or 0,
            },
            tenant_id=request.tenant_id,
        )