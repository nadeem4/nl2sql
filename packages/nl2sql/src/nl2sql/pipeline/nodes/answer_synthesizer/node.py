from __future__ import annotations

import json
from typing import Dict, Any, List, Optional, TYPE_CHECKING

from langchain_core.prompts import ChatPromptTemplate

if TYPE_CHECKING:
    from nl2sql.pipeline.state import GraphState

from nl2sql.common.errors import PipelineError, ErrorSeverity, ErrorCode
from nl2sql.common.logger import get_logger
from nl2sql.common.settings import settings
from nl2sql.context import NL2SQLContext
from nl2sql.llm.failures import provider_error
from nl2sql.llm.wires import structured
from .schemas import AggregatedResponse, AnswerSynthesizerResponse
from .prompts import ANSWER_SYNTHESIZER_PROMPT

logger = get_logger("answer_synthesizer")


def _missing_values(warnings: List[Dict[str, Any]]) -> List[str]:
    """The run's "there is no Genre with Name 'Polka'" notes.

    Only ``VALUE_NOT_FOUND`` warnings: an ERROR one belonged to an attempt a
    retry has since replaced.
    """
    notes: List[str] = []
    for w in warnings or []:
        if w.get("error_code") == "VALUE_NOT_FOUND" and w.get("severity") == "WARNING":
            if w.get("message") and w["message"] not in notes:
                notes.append(w["message"])
    return notes


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


class AnswerSynthesizerNode:
    """Summarizes aggregated results into a user-facing answer."""

    def __init__(self, ctx: NL2SQLContext):
        self.node_name = self.__class__.__name__.lower().replace("node", "")
        self.llm = ctx.llm_registry.get_llm(self.node_name)
        self.prompt = ChatPromptTemplate.from_template(ANSWER_SYNTHESIZER_PROMPT)
        self.chain = self.prompt | structured(self.llm, AggregatedResponse)

    def _serialize_result(self, result: Any, saved: Optional[Dict[str, Any]] = None) -> str:
        if isinstance(result, dict) and all(isinstance(v, list) for v in result.values()):
            result = result_preview(result, int(settings.answer_preview_rows), saved)
        try:
            return json.dumps(result, indent=2, ensure_ascii=True, default=str)
        except TypeError:
            return str(result)

    def __call__(self, state: GraphState) -> Dict[str, Any]:
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
            notes = _missing_values(state.warnings)
            cut = _cut_results(state)
            response: AggregatedResponse = self.chain.invoke(
                {
                    "user_query": state.user_query,
                    "result_preview": self._serialize_result(aggregated_result, saved),
                    "result_limit": "\n".join(f"- {c}" for c in cut) or "None",
                    "unmapped_subqueries": json.dumps(
                        unmapped_subqueries, indent=2, ensure_ascii=True
                    ),
                    "data_notes": "\n".join(f"- {n}" for n in notes) or "None",
                }
            )
            # Said whatever the model wrote: an empty result on a value that
            # does not exist is not a finding about the data.
            # Likewise a cut result is said to be cut, whatever the model wrote.
            said = list(response.warnings or [])
            response.warnings = said + [n for n in notes + cut if n not in said]
            final_answer = response.model_dump()
            final_answer["truncated"] = bool(cut)

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
