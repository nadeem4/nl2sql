from __future__ import annotations
from typing import Dict, Any, List, Optional, TYPE_CHECKING
import traceback
from concurrent.futures import ProcessPoolExecutor, TimeoutError

if TYPE_CHECKING:
    from nl2sql.pipeline.state import SubgraphExecutionState

from nl2sql.common.errors import PipelineError, ErrorSeverity, ErrorCode
from nl2sql.datasources.discovery import discover_adapters
from nl2sql.common.logger import get_logger
from nl2sql.pipeline.nodes.validator.schemas import PhysicalValidatorResponse
from nl2sql.common.sandbox import get_execution_pool
from nl2sql.datasources.protocols import DatasourceAdapterProtocol
from nl2sql.context import NL2SQLContext

logger = get_logger("physical_validator")

from nl2sql.common.contracts import ExecutionRequest, ExecutionResult

def _dry_run_in_process(request: ExecutionRequest) -> ExecutionResult:
    """Executes dry run in a separate process."""
    available = discover_adapters()
    if request.engine_type not in available:
        return ExecutionResult(success=False, error=f"Unknown engine: {request.engine_type}")
    
    try:
        adapter_cls = available[request.engine_type]
        adapter = adapter_cls(
            datasource_id=request.datasource_id,
            datasource_engine_type=request.engine_type,
            connection_args=request.connection_args
        )
        res = adapter.dry_run(request.sql)
        return ExecutionResult(
            success=True,
            data={"is_valid": res.is_valid, "error_message": res.error_message}
        )
    except Exception as e:
        return ExecutionResult(success=False, error=str(e))

def _cost_estimate_in_process(request: ExecutionRequest) -> ExecutionResult:
    """Executes cost estimation in a separate process."""
    available = discover_adapters()
    if request.engine_type not in available:
        return ExecutionResult(success=False, error=f"Unknown engine: {request.engine_type}")
    
    try:
        adapter_cls = available[request.engine_type]
        adapter = adapter_cls(
            datasource_id=request.datasource_id,
            datasource_engine_type=request.engine_type,
            connection_args=request.connection_args
        )
        cost = adapter.cost_estimate(request.sql)
        return ExecutionResult(
            success=True,
            data=cost.model_dump()
        )
    except Exception as e:
        return ExecutionResult(success=False, error=str(e))


class PhysicalValidatorNode:
    """Validates the Generated SQL for Executability and Performance.

    Safety and Authorization are established by the LogicalValidator on the AST.
    This node focuses on physical execution properties:
    1. Semantics: The query executes cleanly via a Dry Run (if supported).
    2. Performance: The query cost is within acceptable limits.

    Attributes:
        registry (DatasourceRegistry): Registry to fetch adapters and dialects.
        row_limit (int | None): Configured maximum row limit for performance warnings.
    """

    def __init__(self, ctx: NL2SQLContext, row_limit: int | None = None):
        """Initializes the PhysicalValidatorNode.

        Args:
            ctx (NL2SQLContext): The context of the pipeline.
            row_limit (int | None): Optional row limit for performance validation.
        """
        self.registry = ctx.ds_registry
        self.row_limit = row_limit

    def _validate_semantic(self, sql: str, adapter: DatasourceAdapterProtocol) -> Optional[PipelineError]:
        """Performs a dry run to check for execution errors.

        This method executes a 'Dry Run' (usually via transaction rollback) inside 
        the Sandboxed Execution Pool.

        Args:
            sql (str): The SQL query.
            adapter (DatasourceAdapterProtocol): The adapter instance.

        Returns:
            Optional[PipelineError]: Error if dry run failed, None otherwise.
        """
        try:
            pool = get_execution_pool()
            request = ExecutionRequest(
                mode="dry_run",
                datasource_id=adapter.datasource_id,
                engine_type=adapter.datasource_engine_type,
                connection_args=adapter.connection_args,
                sql=sql
            )
            
            from nl2sql.common.sandbox import execute_in_sandbox
            from nl2sql.common.resilience import DB_BREAKER
            import pybreaker
            
            @DB_BREAKER
            def _guarded_semantic():
                 res = execute_in_sandbox(pool, _dry_run_in_process, request)
                 if not res.success:
                     if res.metrics.get("is_crash"):
                         raise RuntimeError(f"Sandbox Crash: {res.error}")
                     if "timed out" in str(res.error).lower():
                         raise TimeoutError(res.error)
                 return res

            res_contract = _guarded_semantic()
            
            if not res_contract.success:
                 # Check for crash
                 if res_contract.metrics.get("is_crash"):
                     logger.error(f"Dry run caused Sandbox Crash: {res_contract.error}")
                     return PipelineError(
                         node="physical_validator",
                         message="Validation Pre-Check caused a Segfault (Sandbox Crash). Query likely unsafe.",
                         severity=ErrorSeverity.CRITICAL,
                         error_code=ErrorCode.EXECUTOR_CRASH
                     )
                 
                 return PipelineError(
                     node="physical_validator",
                     message=f"Dry Run Failed: {res_contract.error}",
                     severity=ErrorSeverity.ERROR,
                     error_code=ErrorCode.EXECUTION_ERROR
                 )

            # Unpack the business result
            data = res_contract.data
            if not data["is_valid"]:
                return PipelineError(
                    node="physical_validator",
                    message=f"Dry Run Failed: {data['error_message']}",
                    severity=ErrorSeverity.ERROR,
                    error_code=ErrorCode.EXECUTION_ERROR
                )

        except pybreaker.CircuitBreakerError:
             logger.warning("DB Circuit Breaker Open during semantic validation.")
             return PipelineError(
                 node="physical_validator",
                 message="Validation skipped (Circuit Breaker Open).",
                 severity=ErrorSeverity.ERROR,
                 error_code=ErrorCode.SERVICE_UNAVAILABLE
             )
        except Exception as e:
            logger.warning(f"Dry run skipped or failed: {e}")
        return None

    def _validate_performance(self, sql: str, adapter: DatasourceAdapterProtocol) -> List[PipelineError]:
        """Checks query cost estimation against configured limits.

        Args:
            sql (str): The SQL query.
            adapter (DatasourceAdapterProtocol): The adapter instance.

        Returns:
            List[PipelineError]: List of performance warnings/errors.
        """
        errors = []
        try:
            pool = get_execution_pool()
            request = ExecutionRequest(
                mode="cost_estimate",
                datasource_id=adapter.datasource_id,
                engine_type=adapter.datasource_engine_type,
                connection_args=adapter.connection_args,
                sql=sql
            )
            
            from nl2sql.common.sandbox import execute_in_sandbox
            from nl2sql.common.resilience import DB_BREAKER
            import pybreaker

            @DB_BREAKER
            def _guarded_performance():
                res = execute_in_sandbox(pool, _cost_estimate_in_process, request)
                if not res.success:
                    if res.metrics.get("is_crash"):
                        raise RuntimeError(f"Sandbox Crash: {res.error}")
                    if "timed out" in str(res.error).lower():
                        raise TimeoutError(res.error)
                return res

            res_contract = _guarded_performance()
            
            if res_contract.success:
                data = res_contract.data
                estimated_rows = data.get("estimated_rows", 0)
                
                if self.row_limit and estimated_rows > self.row_limit:
                     errors.append(PipelineError(
                         node="physical_validator",
                         message=f"Estimated {estimated_rows} rows exceeds limit {self.row_limit}",
                         severity=ErrorSeverity.WARNING,
                         error_code=ErrorCode.PERFORMANCE_WARNING
                     ))
            else:
                 logger.warning(f"Cost estimation failed: {res_contract.error}")

        except pybreaker.CircuitBreakerError:
            logger.warning("Cost estimation skipped (Circuit Breaker Open).")
            # We don't return error here, just skip performance check
        except Exception as e:
             logger.warning(f"Performance check skipped: {e}")
        return errors

    def __call__(self, state: SubgraphExecutionState) -> Dict[str, Any]:
        """Executes the physical validation.

        Args:
            state (GraphState): Current execution state.

        Returns:
            Dict[str, Any]: Validation results.
        """
        node_name = "physical_validator"
        errors: List[PipelineError] = []
        
        sql = state.generator_response.sql_draft if state.generator_response else None
        if not sql:
            return {"physical_validator_response": PhysicalValidatorResponse()}

        try:
            ds_id = state.sub_query.datasource_id if state.sub_query else None
            if not ds_id:
                 error = PipelineError(
                     node=node_name,
                     message="No datasource ID.",
                     severity=ErrorSeverity.ERROR,
                     error_code=ErrorCode.MISSING_DATASOURCE_ID,
                 )
                 return {
                    "physical_validator_response": PhysicalValidatorResponse(errors=[error]),
                    "errors": [error],
                 }

            adapter = self.registry.get_adapter(ds_id)

            sem_err = self._validate_semantic(sql, adapter)
            if sem_err:
                errors.append(sem_err)
            
            if sem_err and sem_err.severity == ErrorSeverity.CRITICAL:
                 pass
            else:
                perf_errors = self._validate_performance(sql, adapter)
                errors.extend(perf_errors)

            reasoning = "Physical validation passed." if not errors else [e.message for e in errors]
            response = PhysicalValidatorResponse(
                errors=errors,
                reasoning=[{"node": node_name, "content": reasoning}],
            )

            return {
                "physical_validator_response": response,
                "errors": errors,
                "reasoning": response.reasoning,
            }

        except Exception as exc:
            logger.exception("Physical Validator crashed")
            error = PipelineError(
                node=node_name,
                message=f"Physical Validator crashed: {exc}",
                severity=ErrorSeverity.ERROR,
                error_code=ErrorCode.PHYSICAL_VALIDATOR_FAILED,
                stack_trace=traceback.format_exc(),
            )
            return {
                "physical_validator_response": PhysicalValidatorResponse(errors=[error]),
                "errors": [error],
            }
