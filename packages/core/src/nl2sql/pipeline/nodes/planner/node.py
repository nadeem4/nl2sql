from __future__ import annotations

import json
from typing import Any, Callable, Dict, Optional, Union, TYPE_CHECKING

from langchain_core.runnables import Runnable
from langchain_core.prompts import ChatPromptTemplate

if TYPE_CHECKING:
    from nl2sql.pipeline.state import GraphState

from .schemas import PlanModel
from .prompts import PLANNER_PROMPT, PLANNER_EXAMPLES
from nl2sql.datasources import DatasourceRegistry
from nl2sql.common.errors import PipelineError, ErrorSeverity, ErrorCode
from nl2sql.common.logger import get_logger

logger = get_logger("planner")

LLMCallable = Union[Callable[[str], str], Runnable]


class PlannerNode:
    """
    Generates a structured SQL execution plan based on an authoritative entity graph
    and schema context.
    """

    def __init__(self, registry: DatasourceRegistry, llm: Optional[LLMCallable] = None):
        self.registry = registry
        self.llm = llm
        if self.llm:
            self.prompt = ChatPromptTemplate.from_template(PLANNER_PROMPT)
            self.chain = self.prompt | self.llm

    def __call__(self, state: GraphState) -> Dict[str, Any]:
        node_name = self.__class__.__name__.lower()

        try:
            if not self.llm:
                return {
                    "errors": [
                        PipelineError(
                            node=node_name,
                            message="Planner LLM not provided; no plan generated.",
                            severity=ErrorSeverity.CRITICAL,
                            error_code=ErrorCode.MISSING_LLM,
                        )
                    ]
                }

            relevant_tables = '\n'.join([table.model_dump_json(indent=2) for table in state.relevant_tables])
            

            errors = '\n'.join([ error.model_dump_json(indent=2) for error in state.errors])

            if errors:
                feedback = f"The previous plan was invalid. Fix the following errors:\n {errors}"
            else:
                feedback = ""
        
            date_format = "ISO 8601 (YYYY-MM-DD)"
            try:
                if state.selected_datasource_id:
                    profile = self.registry.get_profile(state.selected_datasource_id)
                    date_format = profile.date_format
            except Exception:
                pass

            semantic_context = "No semantic context available."
            if state.semantic_analysis:
                semantic_context = state.semantic_analysis.model_dump_json(indent=2)

            plan_model: PlanModel = self.chain.invoke(
                {
                    "relevant_tables": relevant_tables,
                    "examples": PLANNER_EXAMPLES,
                    "feedback": feedback,
                    "user_query": state.user_query,
                    "date_format": date_format,
                    "semantic_context": semantic_context,
                }
            )
            reasoning = plan_model.reasoning or "No reasoning provided."
            planner_thoughts = [
                f"Reasoning: {reasoning}",
                f"Tables: {', '.join([t.name for t in plan_model.tables])}",
            ]

            return {
                "plan": plan_model,
                "reasoning": [
                    {"node": node_name, "content": planner_thoughts}
                ],
                "errors": [],
            }

        except Exception as exc:
            return {
                "plan": None,
                "errors": [
                    PipelineError(
                        node=node_name,
                        message=f"Planner failed. Error: {repr(exc)}",
                        severity=ErrorSeverity.ERROR,
                        error_code=ErrorCode.PLANNING_FAILURE,
                        stack_trace=str(exc),
                    )
                ],
            }
