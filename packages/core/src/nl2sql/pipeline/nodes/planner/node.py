from __future__ import annotations
import traceback
from typing import Any, Dict, Optional, TYPE_CHECKING
from langchain_core.runnables import Runnable
from langchain_core.prompts import ChatPromptTemplate

from .prompts import PLANNER_PROMPT, PLANNER_EXAMPLES
from .schemas import PlanModel
from nl2sql.datasources import DatasourceRegistry
from nl2sql.common.errors import PipelineError, ErrorSeverity, ErrorCode
from nl2sql.common.logger import get_logger

if TYPE_CHECKING:
    from nl2sql.pipeline.state import GraphState

logger = get_logger("planner")


class PlannerNode:
    """
    Generates a structured SQL execution plan (PlanModel)
    using LLM + deterministic AST validation.
    """

    def __init__(self, registry: DatasourceRegistry, llm: Optional[Runnable] = None):
        self.registry = registry
        self.llm = llm

        if self.llm:
            prompt = ChatPromptTemplate.from_template(PLANNER_PROMPT)
            self.chain = prompt | self.llm

    def __call__(self, state: GraphState) -> Dict[str, Any]:
        node_name = "planner"

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

        try:
            relevant_tables = '\n'.join(
                t.model_dump_json(indent=2) for t in state.relevant_tables
            )


            feedback = ""
            if state.errors:
                feedback = "\n".join(e.model_dump_json(indent=2) for e in state.errors)

            date_format = "ISO 8601 (YYYY-MM-DD)"
            try:
                if state.selected_datasource_id:
                    profile = self.registry.get_profile(state.selected_datasource_id)
                    date_format = profile.date_format
            except Exception as e:
                logger.debug(f"Failed to fetch datasource profile: {e}")

            semantic_context = (
                state.semantic_analysis.model_dump_json(indent=2)
                if state.semantic_analysis
                else "No semantic context available."
            )

            plan: PlanModel = self.chain.invoke(
                {
                    "relevant_tables": relevant_tables,
                    "examples": PLANNER_EXAMPLES,
                    "feedback": feedback,
                    "user_query": state.user_query,
                    "date_format": date_format,
                    "semantic_context": semantic_context,
                }
            )

            return {
                "plan": plan,
                "reasoning": [
                    {
                        "node": node_name,
                        "content": [
                            f"Reasoning: {plan.reasoning or 'None'}",
                            f"Tables: {', '.join(t.name for t in plan.tables)}",
                        ],
                    }
                ],
                "errors": [],
            }

        except Exception as exc:
            logger.exception("Planner failed")
            return {
                "plan": None,
                "errors": [
                    PipelineError(
                        node=node_name,
                        message="Planner failed.",
                        severity=ErrorSeverity.ERROR,
                        error_code=ErrorCode.PLANNING_FAILURE,
                        stack_trace=traceback.format_exc(),
                    )
                ],
            }
