from __future__ import annotations
from typing import Dict, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from nl2sql.pipeline.state import GraphState

from nl2sql.common.errors import PipelineError, ErrorSeverity, ErrorCode
from nl2sql.pipeline.nodes.aggregator.schemas import AggregatorResponse
from nl2sql.common.logger import get_logger
from nl2sql.context import NL2SQLContext
from nl2sql.aggregation import AggregationService
from nl2sql.aggregation.engines.polars_duckdb import PolarsDuckdbEngine

logger = get_logger("aggregator")


class EngineAggregatorNode:
    """Thin wrapper for aggregation service using ExecutionDAG layers."""

    def __init__(self, ctx: NL2SQLContext):
        self.node_name = self.__class__.__name__.lower().replace("node", "")
        self.ctx = ctx
        self.service = AggregationService(PolarsDuckdbEngine())

    def __call__(self, state: GraphState) -> Dict[str, Any]:
        try:
            dag = getattr(state.global_planner_response, "execution_dag", None)
            artifact_refs = state.artifact_refs or {}
            terminal_results = self.service.execute(dag, artifact_refs)
            aggregator_response = AggregatorResponse(
                terminal_results=terminal_results,
                computed_artifacts={},
            )
            return {
                "aggregator_response": aggregator_response,
                "reasoning": [{"node": self.node_name, "content": "ExecutionDAG aggregation executed successfully."}],
            }
        except Exception as exc:
            logger.error(f"Node {self.node_name} failed: {exc}")
            return {
                "aggregator_response": AggregatorResponse(),
                "reasoning": [{"node": self.node_name, "content": f"Error: {str(exc)}", "type": "error"}],
                "errors": [
                    PipelineError(
                        node=self.node_name,
                        message=f"Aggregator failed: {str(exc)}",
                        severity=ErrorSeverity.ERROR,
                        error_code=ErrorCode.AGGREGATOR_FAILED,
                    )
                ],
            }
