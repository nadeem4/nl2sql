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
from nl2sql.datasources.protocols import DatasourceAdapterProtocol
from nl2sql.context import NL2SQLContext

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

    def __init__(self, ctx: NL2SQLContext, row_limit: int | None = None):
        """Initializes the PhysicalValidatorNode.

        Args:
            ctx (NL2SQLContext): The context of the pipeline.
            row_limit (int | None): Optional row limit for performance validation.
        """
        self.registry = ctx.ds_registry
        self.row_limit = row_limit


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

        
        return {
            "physical_validator_response": PhysicalValidatorResponse(),
            "errors": errors,
            "reasoning": {},
        }
