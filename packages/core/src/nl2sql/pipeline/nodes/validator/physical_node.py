from __future__ import annotations
from typing import Dict, Any, List, Optional
import traceback

from nl2sql.pipeline.state import GraphState
from nl2sql.common.errors import PipelineError, ErrorSeverity, ErrorCode
from nl2sql.datasources import DatasourceRegistry
from nl2sql.common.logger import get_logger

logger = get_logger("physical_validator")


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

    def _validate_semantic(self, sql: str, adapter) -> Optional[PipelineError]:
        """Performs a dry run to check for execution errors."""
        try:
            res = adapter.dry_run(sql)
            if not res.is_valid:
                return PipelineError(
                    node="physical_validator",
                    message=f"Dry Run Failed: {res.error_message}",
                    severity=ErrorSeverity.ERROR,
                    error_code=ErrorCode.EXECUTION_ERROR
                )
        except Exception as e:
            logger.warning(f"Dry run skipped or failed: {e}")
        return None

    def _validate_performance(self, sql: str, adapter) -> List[PipelineError]:
        """Checks query cost estimation."""
        errors = []
        try:
            cost = adapter.cost_estimate(sql)
            if self.row_limit and cost.estimated_rows > self.row_limit:
                 errors.append(PipelineError(
                     node="physical_validator",
                     message=f"Estimated {cost.estimated_rows} rows exceeds limit {self.row_limit}",
                     severity=ErrorSeverity.WARNING,
                     error_code=ErrorCode.PERFORMANCE_WARNING
                 ))
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
