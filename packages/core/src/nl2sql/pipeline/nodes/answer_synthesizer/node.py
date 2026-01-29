from __future__ import annotations

import json
from typing import Dict, Any, TYPE_CHECKING

from langchain_core.prompts import ChatPromptTemplate

if TYPE_CHECKING:
    from nl2sql.pipeline.state import GraphState

from nl2sql.common.errors import PipelineError, ErrorSeverity, ErrorCode
from nl2sql.common.logger import get_logger
from nl2sql.context import NL2SQLContext
from .schemas import AggregatedResponse, AnswerSynthesizerResponse
from .prompts import ANSWER_SYNTHESIZER_PROMPT

logger = get_logger("answer_synthesizer")


class AnswerSynthesizerNode:
    """Summarizes aggregated results into a user-facing answer."""

    def __init__(self, ctx: NL2SQLContext):
        self.node_name = self.__class__.__name__.lower().replace("node", "")
        self.llm = ctx.llm_registry.get_llm(self.node_name)
        self.prompt = ChatPromptTemplate.from_template(ANSWER_SYNTHESIZER_PROMPT)
        self.chain = self.prompt | self.llm.with_structured_output(
            AggregatedResponse, method="function_calling"
        )

    def _serialize_result(self, result: Any) -> str:
        try:
            return json.dumps(result, indent=2, ensure_ascii=True)
        except TypeError:
            return str(result)

    def __call__(self, state: GraphState) -> Dict[str, Any]:
        aggregated_result = None
        if state.aggregator_response:
            aggregated_result = state.aggregator_response.terminal_results
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
            response: AggregatedResponse = self.chain.invoke(
                {
                    "user_query": state.user_query,
                    "aggregated_result": self._serialize_result(aggregated_result),
                    "unmapped_subqueries": json.dumps(
                        unmapped_subqueries, indent=2, ensure_ascii=True
                    ),
                }
            )

            return {
                "answer_synthesizer_response": AnswerSynthesizerResponse(
                    final_answer=response.model_dump(),
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
                    PipelineError(
                        node=self.node_name,
                        message=f"Answer synthesis failed: {exc}",
                        severity=ErrorSeverity.ERROR,
                        error_code=ErrorCode.AGGREGATOR_FAILED,
                    )
                ]
            }
