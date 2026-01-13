from __future__ import annotations
from typing import Dict, Any, List, Optional, TYPE_CHECKING
import traceback
from concurrent.futures import ProcessPoolExecutor, TimeoutError

if TYPE_CHECKING:
    from nl2sql.pipeline.state import GraphState

from nl2sql.common.errors import PipelineError, ErrorSeverity, ErrorCode
from nl2sql.datasources import DatasourceRegistry
from nl2sql.datasources.discovery import discover_adapters
from nl2sql.common.logger import get_logger
from nl2sql.common.sandbox import get_execution_pool
from nl2sql_adapter_sdk import DatasourceAdapter, DryRunResult, CostEstimate

logger = get_logger("physical_validator")

def _dry_run_in_process(
    engine_type: str,
    ds_id: str,
    connection_args: Dict[str, Any],
    sql: str
) -> DryRunResult:
    """Executes dry run in a separate process."""
    available = discover_adapters()
    if engine_type not in available:
        raise ValueError(f"Unknown datasource engine type: {engine_type}")
    
    adapter_cls = available[engine_type]
    adapter = adapter_cls(
        datasource_id=ds_id,
        datasource_engine_type=engine_type,
        connection_args=connection_args
    )
    return adapter.dry_run(sql)

def _cost_estimate_in_process(
    engine_type: str,
    ds_id: str,
    connection_args: Dict[str, Any],
    sql: str
) -> CostEstimate:
    """Executes cost estimation in a separate process."""
    available = discover_adapters()
    if engine_type not in available:
        raise ValueError(f"Unknown datasource engine type: {engine_type}")
    
    adapter_cls = available[engine_type]
    adapter = adapter_cls(
        datasource_id=ds_id,
        datasource_engine_type=engine_type,
        connection_args=connection_args
    )
    return adapter.cost_estimate(sql)


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

    def __init__(self, registry: DatasourceRegistry, row_limit: int | None = None):
        """Initializes the PhysicalValidatorNode.

        Args:
            registry (DatasourceRegistry): The registry of datasources.
            row_limit (int | None): Optional row limit for performance validation.
        """
        self.registry = registry
        self.row_limit = row_limit

    def _validate_semantic(self, sql: str, adapter: DatasourceAdapter) -> Optional[PipelineError]:
        """Performs a dry run to check for execution errors.

        This method executes a 'Dry Run' (usually via transaction rollback) inside 
        the Sandboxed Execution Pool.

        Args:
            sql (str): The SQL query.
            adapter (DatasourceAdapter): The adapter instance.

        Returns:
            Optional[PipelineError]: Error if dry run failed, None otherwise.
        """
        try:
            pool = get_execution_pool()
            future = pool.submit(
                _dry_run_in_process,
                engine_type=adapter.datasource_engine_type,
                ds_id=adapter.datasource_id,
                connection_args=adapter.connection_args,
                sql=sql
            )
            
            res = future.result(timeout=10) 
            
            if not res.is_valid:
                return PipelineError(
                    node="physical_validator",
                    message=f"Dry Run Failed: {res.error_message}",
                    severity=ErrorSeverity.ERROR,
                    error_code=ErrorCode.EXECUTION_ERROR
                )
        except TimeoutError:
             logger.warning("Dry run timed out. Skipping.")
        except Exception as e:
            msg = str(e)
            if "BrokenProcessPool" in msg:
                 logger.error(f"Dry run caused Sandbox Crash: {msg}")
                 return PipelineError(
                     node="physical_validator",
                     message="Validation Pre-Check caused a Segfault (Sandbox Crash). Query likely unsafe.",
                     severity=ErrorSeverity.CRITICAL,
                     error_code=ErrorCode.EXECUTOR_CRASH
                 )
            logger.warning(f"Dry run skipped or failed: {e}")
        return None

    def _validate_performance(self, sql: str, adapter: DatasourceAdapter) -> List[PipelineError]:
        """Checks query cost estimation against configured limits.

        Args:
            sql (str): The SQL query.
            adapter (DatasourceAdapter): The adapter instance.

        Returns:
            List[PipelineError]: List of performance warnings/errors.
        """
        errors = []
        try:
            pool = get_execution_pool()
            future = pool.submit(
                _cost_estimate_in_process,
                engine_type=adapter.datasource_engine_type,
                ds_id=adapter.datasource_id,
                connection_args=adapter.connection_args,
                sql=sql
            )
            cost = future.result(timeout=10)
            
            if self.row_limit and cost.estimated_rows > self.row_limit:
                 errors.append(PipelineError(
                     node="physical_validator",
                     message=f"Estimated {cost.estimated_rows} rows exceeds limit {self.row_limit}",
                     severity=ErrorSeverity.WARNING,
                     error_code=ErrorCode.PERFORMANCE_WARNING
                 ))
        except TimeoutError:
            logger.warning("Cost estimation timed out. Skipping.")
        except Exception as e:
            logger.warning(f"Performance check skipped: {e}")
        return errors

    def __call__(self, state: GraphState) -> Dict[str, Any]:
        """Executes the physical validation.

        Args:
            state (GraphState): Current execution state.

        Returns:
            Dict[str, Any]: Validation results.
        """
        node_name = "physical_validator"
        errors: List[PipelineError] = []
        
        sql = state.sql_draft
        if not sql:
            return {}

        try:
            ds_id = state.selected_datasource_id
            if not ds_id:
                 return {
                    "errors": [PipelineError(
                        node=node_name, message="No datasource ID.", 
                        severity=ErrorSeverity.ERROR, error_code=ErrorCode.MISSING_DATASOURCE_ID
                    )]
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

            return {
                "errors": errors,
                "reasoning": [{"node": node_name, "content": reasoning}]
            }

        except Exception as exc:
            logger.exception("Physical Validator crashed")
            return {
                "errors": [
                    PipelineError(
                        node=node_name,
                        message=f"Physical Validator crashed: {exc}",
                        severity=ErrorSeverity.ERROR,
                        error_code=ErrorCode.PHYSICAL_VALIDATOR_FAILED,
                        stack_trace=traceback.format_exc(),
                    )
                ]
            }
