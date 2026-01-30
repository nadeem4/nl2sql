from __future__ import annotations
import traceback
from typing import Any, Dict, Optional, TYPE_CHECKING
from langchain_core.runnables import Runnable
from langchain_core.prompts import ChatPromptTemplate

from .prompts import PLANNER_PROMPT, PLANNER_EXAMPLES
from .schemas import PlanModel, ASTPlannerResponse
from nl2sql.common.errors import PipelineError, ErrorSeverity, ErrorCode
from nl2sql.common.logger import get_logger
from nl2sql.context import NL2SQLContext

if TYPE_CHECKING:
    from nl2sql.pipeline.state import SubgraphExecutionState

logger = get_logger("planner")


class ASTPlannerNode:
    """Generates a structured SQL execution plan (PlanModel).

    Uses an LLM to interpret the user query and semantic context, producing a
    deterministic Abstract Syntax Tree (AST) that represents the SQL query.

    Attributes:
        llm (Optional[Runnable]): The Language Model executable.
        chain (Optional[Runnable]): The langchain chain for planning.
    """

    def __init__(self, ctx: NL2SQLContext):
        """Initializes the PlannerNode.

        Args:
            ctx (NL2SQLContext): The context of the pipeline.
        """
        self.node_name = self.__class__.__name__.lower().replace('node', '')
        self.llm = ctx.llm_registry.get_llm(self.node_name)

        self.prompt = ChatPromptTemplate.from_template(PLANNER_PROMPT)
        self.chain = self.prompt | self.llm.with_structured_output(PlanModel)

    def __call__(self, state: SubgraphExecutionState) -> Dict[str, Any]:
        """Executes the planning node.

        Args:
           state (GraphState): The current state of the execution graph.

        Returns:s
            Dict[str, Any]: A dictionary containing the generated 'plan', 'reasoning',
                and any 'errors' encountered.
        """
        try:
            relevant_tables = '\n'.join(
                t.model_dump_json(indent=2) for t in state.relevant_tables
            )


            feedback = ""
            if state.errors:
                feedback = "\n".join(e.model_dump_json(indent=2) for e in state.errors)

            query_text = state.sub_query.intent if state.sub_query else ""
            expected_schema = []
            if state.sub_query and state.sub_query.expected_schema:
                expected_schema = [c.model_dump() for c in state.sub_query.expected_schema]
            plan: PlanModel = self.chain.invoke(
                {
                    "relevant_tables": relevant_tables,
                    "examples": PLANNER_EXAMPLES,
                    "feedback": feedback,
                    "expected_schema": expected_schema,
                    "semantic_context": "",
                    "user_query": query_text,
                }
            )

            logger.info(f"Generated Plan: {plan.model_dump_json(indent=2)}")

            return {
                "ast_planner_response": ASTPlannerResponse(plan=plan),
                "reasoning": [
                    {
                        "node": self.node_name,
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
                "ast_planner_response": ASTPlannerResponse(plan=None),
                "errors": [
                    PipelineError(
                        node=self.node_name,
                        message="Planner failed.",
                        severity=ErrorSeverity.ERROR,
                        error_code=ErrorCode.PLANNING_FAILURE,
                        stack_trace=traceback.format_exc(),
                    )
                ],
            }
