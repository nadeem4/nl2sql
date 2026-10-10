from __future__ import annotations
from typing import Dict, Any, List, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from nl2sql.pipeline.state import GraphState

from nl2sql.common.errors import PipelineError, ErrorSeverity, ErrorCode
from nl2sql.pipeline.nodes.aggregator.schemas import AggregatorResponse
from nl2sql.common.logger import get_logger
from nl2sql.context import NL2SQLContext
from nl2sql.aggregation import AggregationService
from nl2sql.aggregation.engines.polars_duckdb import PolarsDuckdbEngine

logger = get_logger("aggregator")

# A capped input to these is refused: missing rows mean missing matches, so the
# answer may be wrong. A union of a capped input is still right, only incomplete.
_TWO_SIDED = {"join", "compare"}


class EngineAggregatorNode:
    """Thin wrapper for aggregation service using ExecutionDAG layers."""

    def __init__(self, ctx: NL2SQLContext):
        self.node_name = self.__class__.__name__.lower().replace("node", "")
        self.ctx = ctx
        self.service = AggregationService(PolarsDuckdbEngine())

    def _capped_combine_inputs(self, state: GraphState) -> List[Tuple[str, str, int]]:
        """``(operation, sub_query_id, row_cap)`` for each combine input that hit its row cap.

        The generator caps a sub-query at the adapter's row limit when the plan
        sets no smaller LIMIT. A result with that many rows may have been cut
        short, and a combine cannot tell: a join silently loses every match the
        missing rows held. Exactly ``row_cap`` rows may also be the whole
        result; that cannot be told apart without fetching more, so it is
        treated as cut short.
        """
        capped: Dict[str, int] = {}
        for output in (getattr(state, "subgraph_outputs", None) or {}).values():
            cap, artifact = output.row_cap, output.artifact
            if output.sub_query and cap and artifact is not None and artifact.row_count >= cap:
                capped[output.sub_query.id] = cap
        found = []
        for node in state.execution_dag.nodes if capped else []:
            if node.kind == "combine":
                for input_id in node.inputs or []:
                    if input_id in capped:
                        found.append((node.attributes.get("operation"), input_id, capped[input_id]))
        return found

    def __call__(self, state: GraphState) -> Dict[str, Any]:
        try:
            dag = state.execution_dag
            capped = self._capped_combine_inputs(state)
            refused = [c for c in capped if c[0] in _TWO_SIDED]
            if refused:
                return self._truncated(refused)
            terminal_results = self.service.execute(dag, state.artifact_refs)
            aggregator_response = AggregatorResponse(
                terminal_results=terminal_results,
                computed_artifacts={},
            )
            return {
                "aggregator_response": aggregator_response,
                "reasoning": [{"node": self.node_name, "content": "ExecutionDAG aggregation executed successfully."}],
                "warnings": [
                    {
                        "node": self.node_name,
                        "message": (
                            f"Sub-query '{sq_id}' returned {cap} rows, the row cap, so its part of the "
                            f"{operation} may be incomplete."
                        ),
                        "error_code": ErrorCode.RESULT_TRUNCATED.value,
                        "severity": ErrorSeverity.WARNING.value,
                        "sub_query_id": sq_id,
                    }
                    for operation, sq_id, cap in capped
                ],
            }
        except Exception as exc:
            logger.error(f"Node {self.node_name} failed: {exc}")
            error = PipelineError(
                node=self.node_name,
                message=f"Aggregator failed: {str(exc)}",
                severity=ErrorSeverity.ERROR,
                error_code=ErrorCode.AGGREGATOR_FAILED,
            )
            # The error on the response is what `aggregator_route` ends the run on.
            return {
                "aggregator_response": AggregatorResponse(errors=[error]),
                "reasoning": [{"node": self.node_name, "content": f"Error: {str(exc)}", "type": "error"}],
                "errors": [error],
            }

    def _truncated(self, refused: List[Tuple[str, str, int]]) -> Dict[str, Any]:
        """Refuses a join/compare over an input cut short by the row cap."""
        parts = [f"sub-query '{sq_id}' returned {cap} rows, the row cap" for _, sq_id, cap in refused]
        operations = sorted({operation for operation, _, _ in refused})
        message = (
            f"Cannot {' or '.join(operations)} complete results: {'; '.join(parts)}, so rows may be "
            f"missing and the combined answer could be wrong. Ask a narrower question, or raise the "
            f"datasource's row_limit."
        )
        logger.error(message)
        error = PipelineError(
            node=self.node_name,
            message=message,
            severity=ErrorSeverity.ERROR,
            error_code=ErrorCode.RESULT_TRUNCATED,
        )
        # The error on the response is what `aggregator_route` ends the run on.
        return {
            "aggregator_response": AggregatorResponse(errors=[error]),
            "reasoning": [{"node": self.node_name, "content": message, "type": "error"}],
            "errors": [error],
        }
