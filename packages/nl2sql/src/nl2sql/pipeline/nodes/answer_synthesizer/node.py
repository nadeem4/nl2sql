from __future__ import annotations

import json
from typing import Dict, Any, List, Optional, TYPE_CHECKING

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableConfig

if TYPE_CHECKING:
    from nl2sql.pipeline.state import GraphState

from nl2sql.common.errors import PipelineError, ErrorSeverity, ErrorCode, RunWarning
from nl2sql.common.logger import get_logger
from nl2sql.hooks import hooks_of
from nl2sql.runtime import Runtime
from nl2sql.llm.failures import provider_error
from nl2sql.llm.wires import streaming_structured, structured
from nl2sql.pipeline.nodes.aggregator.node import write_manifest
from .schemas import AggregatedResponse, AnswerSynthesizerResponse
from .prompts import ANSWER_SYNTHESIZER_PROMPT, VERIFIED_QUERY_SECTION

logger = get_logger("answer_synthesizer")


def _notes(warnings: List[RunWarning], *codes: str) -> List[str]:
    """The messages of the run's warnings with one of ``codes``, each once.

    Not a recovered one: it belonged to an attempt a retry has since replaced.
    """
    notes: List[str] = []
    for w in warnings or []:
        if w.code in codes and not w.detail.get("recovered"):
            if w.message and w.message not in notes:
                notes.append(w.message)
    return notes


# The sanity checks' findings the answer is told about and says: "there is no
# Genre with Name 'Polka'", a sum over rows a join repeated, a column with no value.
_DATA_NOTES = ("VALUE_NOT_FOUND", "JOIN_FAN_OUT", "ALL_NULL_COLUMN")


def _data_notes(warnings: List[RunWarning]) -> List[str]:
    """The run's sanity-check notes (``executor/sanity/``)."""
    return _notes(warnings, *_DATA_NOTES)


def result_preview(result: Dict[str, Any], preview_rows: int,
                   saved: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """What the answer model sees of each terminal result: never the whole of it.

    The first ``preview_rows`` rows, the total ``row_count``, the column names,
    and ``partial`` when the preview leaves rows out. The full result is shown
    to the user as a table; the model only writes about it. ``saved`` holds the
    aggregator's artifact for each result (``computed_artifacts``): its row
    count and columns are the whole result's, where ``result`` holds only the
    first rows.
    """
    preview: Dict[str, Any] = {}
    for node_id, rows in result.items():
        rows = list(rows or [])
        artifact = (saved or {}).get(node_id)
        row_count = artifact.row_count if artifact is not None else len(rows)
        columns = list(rows[0].keys()) if rows and isinstance(rows[0], dict) else []
        preview[node_id] = {
            "rows": rows[:preview_rows],
            "row_count": row_count,
            "columns": list(artifact.columns) if artifact is not None else columns,
            "partial": row_count > preview_rows,
        }
    return preview


def _cut_results(state: GraphState) -> List[str]:
    """One user-facing note per sub-query whose result reached its row ceiling."""
    cut = [o for o in (getattr(state, "subgraph_outputs", None) or {}).values() if o.truncated]
    notes = []
    for output in cut:
        whose = f"The result of sub-query '{output.sub_query.id}'" if len(cut) > 1 and output.sub_query else "The result"
        notes.append(
            f"{whose} was cut at {output.row_cap:,} rows, the row limit, so it is incomplete. "
            f"Narrow the question (a filter, or a top N) to get a complete result."
        )
    return notes


class AnswerGateNode:
    """What a run with ``answer=False`` has in place of the synthesizer: ``answer.after`` and nothing else.

    No model call and no prose (``final_answer`` stays None), but the hook still sees the run
    (``final_answer=None`` in its context) and a ``Deny`` still withholds the data, as it does after an
    answer. It is in the graph only while a hook is attached at ``answer.after``.
    """

    def __init__(self, ctx: Runtime):
        self.node_name = "answer_gate"
        self.ctx = ctx
        self.hooks = hooks_of(ctx)

    def __call__(self, state: GraphState) -> Dict[str, Any]:
        results = state.aggregator_response.results if state.aggregator_response else None
        verdict = self.hooks.call("answer.after", trace_id=state.trace_id, question=state.user_query,
                                  user=state.user_context, datasource_id=state.datasource_id,
                                  final_answer=None, results=results)
        if not verdict.allowed:
            return {"errors": [verdict.error(self.node_name)]}
        release_results(self.ctx, state)
        return {"results_released": True}


def release_results(ctx: Runtime, state: GraphState) -> None:
    """Writes the run's result manifest once its ``answer.after`` hooks allowed; until then nothing is readable.

    The aggregator leaves it unwritten while a hook is attached at ``answer.after``
    (``EngineAggregatorNode._write_manifest``); a run such a hook ends never has one. Without such a hook the
    aggregator has written it already and this does nothing. A failed write fails the run.
    """
    hooks = hooks_of(ctx)
    if hooks.attached("answer.after") and state.aggregator_response is not None:
        write_manifest(ctx, state.trace_id, list(state.aggregator_response.results))


class AnswerSynthesizerNode:
    """Summarizes aggregated results into a user-facing answer."""

    def __init__(self, ctx: Runtime):
        self.node_name = self.__class__.__name__.lower().replace("node", "")
        self.ctx = ctx
        self.preview_rows = ctx.settings.results.preview_rows
        self.hooks = hooks_of(ctx)
        self.llm = ctx.llm_registry.get_llm(self.node_name)
        self.prompt = ChatPromptTemplate.from_template(ANSWER_SYNTHESIZER_PROMPT)
        self.chain = self.prompt | structured(self.llm, AggregatedResponse)
        # Built on first use: (streaming, verified) -> chain.
        self._chains: Dict[tuple, Any] = {}

    def _chain_for(self, config: Optional[RunnableConfig], verified: bool = False):
        """The chain to answer with: streaming when the run asked for it (``stream_query``).

        Same prompt, same parser, same answer; the provider is asked to stream
        so the run's callbacks see the answer as it is written. A verified
        query's answer adds one section to the prompt, its description, so a
        generated answer's prompt is exactly what it was.
        """
        # Not while a hook is attached at answer.after: a streamed answer is
        # already the caller's, and a deny there must be able to withhold it.
        stream = (bool(((config or {}).get("configurable") or {}).get("stream_answer"))
                  and not self.hooks.attached("answer.after"))
        if not stream and not verified:
            return self.chain
        key = (stream, verified)
        if key not in self._chains:
            prompt = (ChatPromptTemplate.from_template(ANSWER_SYNTHESIZER_PROMPT + VERIFIED_QUERY_SECTION)
                      if verified else self.prompt)
            wire = streaming_structured if stream else structured
            self._chains[key] = prompt | wire(self.llm, AggregatedResponse)
        return self._chains[key]

    def _serialize_result(self, result: Any, saved: Optional[Dict[str, Any]] = None) -> str:
        if isinstance(result, dict) and all(isinstance(v, list) for v in result.values()):
            result = result_preview(result, self.preview_rows, saved)
        try:
            return json.dumps(result, indent=2, ensure_ascii=True, default=str)
        except TypeError:
            return str(result)

    def __call__(self, state: GraphState, config: Optional[RunnableConfig] = None) -> Dict[str, Any]:
        out = self._answer(state, config)
        if not out.get("errors"):
            # Outside the answer's own error handling: a failed write fails the run, as a crash.
            release_results(self.ctx, state)
            out["results_released"] = True
        return out

    def _answer(self, state: GraphState, config: Optional[RunnableConfig]) -> Dict[str, Any]:
        aggregated_result = None
        saved = None
        if state.aggregator_response:
            aggregated_result = state.aggregator_response.terminal_results
            saved = state.aggregator_response.computed_artifacts
        elif state.answer_synthesizer_response and state.answer_synthesizer_response.final_answer is not None:
            aggregated_result = state.answer_synthesizer_response.final_answer

        if aggregated_result is None:
            return {
                "errors": [
                    PipelineError(
                        node=self.node_name,
                        message="No aggregated result available for synthesis.",
                        severity=ErrorSeverity.ERROR,
                        error_code=ErrorCode.INVALID_STATE,
                    )
                ]
            }

        try:
            unmapped_subqueries = []
            if state.decomposer_response:
                unmapped_subqueries = [
                    u.model_dump()
                    for u in (state.decomposer_response.unmapped_subqueries or [])
                ]
            notes = _data_notes(state.warnings)
            cut = _cut_results(state)
            inputs = {
                "user_query": state.user_query,
                "result_preview": self._serialize_result(aggregated_result, saved),
                "result_limit": "\n".join(f"- {c}" for c in cut) or "None",
                "unmapped_subqueries": json.dumps(
                    unmapped_subqueries, indent=2, ensure_ascii=True
                ),
                "data_notes": "\n".join(f"- {n}" for n in notes) or "None",
            }
            match = getattr(state, "verified_match", None)
            if match is not None:
                inputs["verified_query_id"] = f"{match.datasource_id}.{match.query_id}"
                inputs["verified_query_description"] = match.description
            response: AggregatedResponse = self._chain_for(config, verified=match is not None).invoke(inputs)
            # Said whatever the model wrote: an empty result on a value that
            # does not exist is not a finding about the data.
            # Likewise a cut result is said to be cut, whatever the model wrote.
            # And a part of the question a hook stopped is said to be missing.
            said = list(response.warnings or [])
            dropped = _notes(state.warnings, "HOOK_DENIED")
            response.warnings = said + [n for n in notes + cut + dropped if n not in said]
            final_answer = response.model_dump()
            final_answer["truncated"] = bool(cut)

            # answer.after: the answer is written; a hook may only withhold it.
            results = state.aggregator_response.results if state.aggregator_response else None
            verdict = self.hooks.call("answer.after", trace_id=state.trace_id, question=state.user_query,
                                      user=state.user_context, datasource_id=state.datasource_id,
                                      final_answer=final_answer, results=results)
            if not verdict.allowed:
                return {"answer_synthesizer_response": AnswerSynthesizerResponse(),
                        "errors": [verdict.error(self.node_name)]}

            return {
                "answer_synthesizer_response": AnswerSynthesizerResponse(
                    final_answer=final_answer,
                ),
                "reasoning": [
                    {
                        "node": self.node_name,
                        "content": response.summary,
                    }
                ],
            }

        except Exception as exc:
            logger.error(f"Node {self.node_name} failed: {exc}")
            return {
                "answer_synthesizer_response": AnswerSynthesizerResponse(),
                "errors": [
                    provider_error(self.node_name, exc) or PipelineError(
                        node=self.node_name,
                        message=f"Answer synthesis failed: {exc}",
                        severity=ErrorSeverity.ERROR,
                        error_code=ErrorCode.AGGREGATOR_FAILED,
                    )
                ]
            }
