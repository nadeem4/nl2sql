from __future__ import annotations
from typing import Dict, Any, List, Set, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from nl2sql.pipeline.state import GraphState

import polars as pl

from nl2sql.common.errors import PipelineError, ErrorSeverity, ErrorCode
from nl2sql.execution.artifacts.results import ResultManifest, ResultSummary
from nl2sql.execution.contracts import ArtifactRef
from nl2sql.pipeline.nodes.aggregator.schemas import AggregatorResponse
from nl2sql.common.logger import get_logger
from nl2sql.runtime import Runtime
from nl2sql.aggregation import AggregationService
from nl2sql.aggregation.engines.polars_duckdb import PolarsDuckdbEngine
from nl2sql.aggregation.join_keys import DTYPE_PLURAL

logger = get_logger("aggregator")

# A capped input to these is refused: missing rows mean missing matches, so the
# answer may be wrong. A union of a capped input is still right, only incomplete.
_TWO_SIDED = {"join", "compare"}


class EngineAggregatorNode:
    """Thin wrapper for aggregation service using ExecutionDAG layers."""

    def __init__(self, ctx: Runtime):
        self.node_name = self.__class__.__name__.lower().replace("node", "")
        self.ctx = ctx
        self.service = AggregationService(PolarsDuckdbEngine(ctx.artifact_store))
        self.preview_rows = ctx.settings.results.preview_rows

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
            dropped = {output.sub_query.id for output in (state.subgraph_outputs or {}).values()
                       if output.status == "dropped" and output.sub_query}
            unconverted: List[Dict[str, Any]] = []
            frames = self.service.execute(dag, state.artifact_refs, dropped, unconverted)
            gone = self._dropped_groups(dag, dropped)
            if dropped and not frames:
                return self._all_dropped(dropped)
            saved = {node_id: self._save(state, node_id, frame) for node_id, frame in frames.items()}
            results = self._write_manifest(state, saved)
            aggregator_response = AggregatorResponse(
                terminal_results={node_id: self._preview(ref) for node_id, ref in saved.items()},
                computed_artifacts=saved,
                results=results,
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
                ] + gone + self._unconverted(state, unconverted),
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

    def _unconverted(self, state: GraphState, found: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """A ``JOIN_KEY_UNCONVERTED`` warning for each side of a join key with values that did not convert."""
        datasources = {sq.id: sq.datasource_id for sq in
                       (state.decomposer_response.sub_queries if state.decomposer_response else [])}
        return [{
            "node": self.node_name,
            "message": (f"{f['failed']} of {f['total']} {datasources.get(f['sub_query_id'], f['sub_query_id'])}."
                        f"{f['column']} values are not {DTYPE_PLURAL[f['dtype']]} and joined nothing."),
            "error_code": ErrorCode.JOIN_KEY_UNCONVERTED.value,
            "severity": ErrorSeverity.WARNING.value,
            "sub_query_id": f["sub_query_id"],
        } for f in found]

    def _dropped_groups(self, dag, dropped: Set[str]) -> List[Dict[str, Any]]:
        """A warning for each combine group a hook's dropped sub-query left unanswered.

        A ``join`` or ``compare`` with one input dropped is dropped whole (it
        would have one side), as is a group whose every input was dropped.
        """
        warnings = []
        for node in dag.nodes if dropped else []:
            inputs = set(node.inputs or [])
            operation = node.attributes.get("operation")
            if node.kind != "combine" or not inputs & dropped:
                continue
            if operation in _TWO_SIDED or inputs <= dropped:
                missing = ", ".join(f"'{i}'" for i in sorted(inputs & dropped))
                warnings.append({
                    "node": self.node_name,
                    "message": (f"The {operation} '{node.attributes.get('group_id')}' was not answered: it needs "
                                f"sub-query {missing}, which a hook stopped."),
                    "error_code": ErrorCode.HOOK_DENIED.value,
                    "severity": ErrorSeverity.WARNING.value,
                })
        return warnings

    def _all_dropped(self, dropped: Set[str]) -> Dict[str, Any]:
        """Hooks dropped every part of the question: the run ends with that, not an empty answer."""
        message = (f"No part of the question could be answered: hooks stopped sub-query "
                   f"{', '.join(repr(i) for i in sorted(dropped))}, and nothing else was left to answer from.")
        error = PipelineError(node=self.node_name, message=message, severity=ErrorSeverity.CRITICAL,
                              error_code=ErrorCode.HOOK_DENIED, details={"scope": "run"})
        # The error on the response is what `aggregator_route` ends the run on.
        return {
            "aggregator_response": AggregatorResponse(errors=[error]),
            "reasoning": [{"node": self.node_name, "content": message, "type": "error"}],
            "errors": [error],
        }

    def _save(self, state: GraphState, node_id: str, frame: pl.LazyFrame) -> ArtifactRef:
        """Writes a terminal result to its own artifact, beside the run's sub-query results.

        The path is the artifact template rendered for the run (``request_id``
        is its trace id) with ``result_<node_id>`` as the sub-query and DAG
        node id; the default template gives ``<tenant>/<run>/result_<node_id>.parquet``.
        A combined result has no subgraph or schema version of its own, so
        a template naming them gets ``aggregator`` and ``result``.
        """
        store = self.service.engine.artifact_store
        ref = store.write_lazy(frame, store.run_metadata(self._tenant_id(), state.trace_id, f"result_{node_id}"))
        return ref.model_copy(update={"sub_query_id": None})

    def _tenant_id(self) -> str:
        return self.ctx.tenant_id

    def _write_manifest(self, state: GraphState, saved: Dict[str, ArtifactRef]) -> List[ResultSummary]:
        """Writes the run's ``manifest.json`` beside its results: each result's columns,
        row count and whether a sub-query feeding it was cut at its row ceiling."""
        cut = {
            output.sub_query.id
            for output in (getattr(state, "subgraph_outputs", None) or {}).values()
            if output.truncated and output.sub_query
        }
        results = [
            ResultSummary(node_id=node_id, columns=list(ref.columns), row_count=ref.row_count,
                          truncated=bool(cut & self._upstream(state, node_id)))
            for node_id, ref in saved.items()
        ]
        self.service.engine.artifact_store.write_manifest(
            self._tenant_id(), state.trace_id, ResultManifest(request_id=state.trace_id, results=results))
        return results

    @staticmethod
    def _upstream(state: GraphState, node_id: str) -> Set[str]:
        """``node_id`` and every DAG node it is built from."""
        inputs = {node.node_id: list(node.inputs or []) for node in state.execution_dag.nodes}
        seen: Set[str] = set()
        pending = [node_id]
        while pending:
            current = pending.pop()
            if current not in seen:
                seen.add(current)
                pending.extend(inputs.get(current, []))
        return seen

    def _preview(self, ref: ArtifactRef) -> List[Dict[str, Any]]:
        """The first ``settings.results.preview_rows`` rows of a saved result, read from its artifact."""
        return self.service.engine.artifact_store.scan_parquet(ref).head(self.preview_rows).collect().to_dicts()

    def _truncated(self, refused: List[Tuple[str, str, int]]) -> Dict[str, Any]:
        """Refuses a join/compare over an input cut short by the row cap."""
        parts = [f"sub-query '{sq_id}' returned {cap} rows, the row cap" for _, sq_id, cap in refused]
        operations = sorted({operation for operation, _, _ in refused})
        message = (
            f"Cannot {' or '.join(operations)} complete results: {'; '.join(parts)}, so rows may be "
            f"missing and the combined answer could be wrong. Ask a narrower question, or raise the "
            f"datasource's row_limit (up to RESULT_ROW_LIMIT)."
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
