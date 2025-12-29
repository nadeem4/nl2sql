from __future__ import annotations

import json
from typing import Any, Callable, Dict, Optional, Union, TYPE_CHECKING

from langchain_core.runnables import Runnable
from langchain_core.prompts import ChatPromptTemplate

if TYPE_CHECKING:
    from nl2sql.core.schemas import GraphState

from .schemas import PlanModel
from .prompts import PLANNER_PROMPT, PLANNER_EXAMPLES
from nl2sql.core.datasource_registry import DatasourceRegistry
from nl2sql.core.errors import PipelineError, ErrorSeverity, ErrorCode
from nl2sql.core.logger import get_logger

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
        node_name = "planner"

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

            if not state.entities:
                return {
                    "errors": [
                        PipelineError(
                            node=node_name,
                            message="Missing entities from Intent node.",
                            severity=ErrorSeverity.CRITICAL,
                            error_code=ErrorCode.INVALID_STATE,
                        )
                    ]
                }

            schema_context = ""
            if state.schema_info:
                schema_context = state.schema_info.model_dump_json(indent=2)

            intent_context = json.dumps(
                [e.model_dump() for e in state.entities], indent=2
            )

            feedback = ""
            if state.errors:
                error_msgs = [e.message for e in state.errors]
                feedback = (
                    "The previous plan was invalid. Fix the following errors:\n"
                    f"{json.dumps(error_msgs, indent=2)}\n"
                )

            date_format = "ISO 8601 (YYYY-MM-DD)"
            try:
                if state.selected_datasource_id:
                    profile = self.registry.get_profile(state.selected_datasource_id)
                    date_format = profile.date_format
            except Exception:
                pass

            plan_model: PlanModel = self.chain.invoke(
                {
                    "schema_context": schema_context,
                    "intent_context": intent_context,
                    "examples": PLANNER_EXAMPLES,
                    "feedback": feedback,
                    "user_query": state.user_query,
                    "date_format": date_format,
                }
            )

            plan_dump = plan_model.model_dump()

            reasoning = plan_model.reasoning or "No reasoning provided."
            planner_thoughts = [
                f"Reasoning: {reasoning}",
                f"Entities: {plan_model.entity_ids}",
                f"Tables: {', '.join([t.name for t in plan_model.tables])}",
            ]

            return {
                "plan": plan_dump,
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
