from __future__ import annotations

import json
from typing import Callable, Optional, Union, Dict, Any, TYPE_CHECKING
from langchain_core.runnables import Runnable

if TYPE_CHECKING:
    from nl2sql.pipeline.state import SubgraphExecutionState
from .prompts import REFINER_PROMPT
from nl2sql.common.errors import PipelineError, ErrorSeverity, ErrorCode
from nl2sql.pipeline.nodes.refiner.schemas import RefinerResponse

from nl2sql.common.logger import get_logger

logger = get_logger("refiner")

LLMCallable = Union[Callable[[str], str], Runnable]

from langchain_core.prompts import ChatPromptTemplate

from langchain_core.output_parsers import StrOutputParser
from nl2sql.context import NL2SQLContext

class RefinerNode:
    """
    Analyzes validation errors and generates constructive feedback for the Planner.

    Uses an LLM to look at the failed plan, the schema, and the errors to suggest fixes.
    """

    def __init__(self, ctx: NL2SQLContext):
        """
        Initializes the RefinerNode.

        Args:
            llm: The language model to use for refinement.
        """
        self.node_name = self.__class__.__name__.lower().replace('node', '')
        self.llm = ctx.llm_registry.get_llm(self.node_name)
        self.prompt = ChatPromptTemplate.from_template(REFINER_PROMPT)
        self.chain = None
        if self.llm is not None:
            self.chain = self.prompt | self.llm | StrOutputParser()

    def __call__(self, state: SubgraphExecutionState) -> Dict[str, Any]:
        """
        Executes the summarization step.

        Args:
            state: The current graph state.

        Returns:
            Dictionary updates for the graph state with refined error messages (feedback).
        """
        try:
            if not self.chain:
                error = PipelineError(
                    node=self.node_name,
                    message="No LLM configured for refiner.",
                    severity=ErrorSeverity.ERROR,
                    error_code=ErrorCode.MISSING_LLM,
                )
                return {
                    "refiner_response": RefinerResponse(errors=[error]),
                    "errors": [error],
                }
            relevant_tables = ""
            if state.relevant_tables:
                lines = []
                for tbl in state.relevant_tables:
                    lines.append(tbl.model_dump_json(indent=2))
                    lines.append("---")
                relevant_tables = "\n".join(lines)

            failed_plan_str = "No plan generated."
            if state.ast_planner_response and state.ast_planner_response.plan:
                try:
                    failed_plan_str = json.dumps(state.ast_planner_response.plan, indent=2)
                except:
                    failed_plan_str = str(state.ast_planner_response.plan)

            # Extract messages from PipelineError objects
            errors_str = "\n".join(f"- {e.message}" for e in state.errors)

            reasoning_str = "No reasoning history."
            if state.reasoning:
                reasoning_str = "\n".join(
                    f"[{r.get('node', 'unknown')}]: {r.get('content')}" for r in state.reasoning
                )

            try:
                feedback = self.chain.invoke({
                    "user_query": state.sub_query.intent if state.sub_query else "",
                    "relevant_tables": relevant_tables,
                    "failed_plan": failed_plan_str,
                    "errors": errors_str,
                    "reasoning": reasoning_str
                })
                
                warning = PipelineError(
                    node=self.node_name,
                    message=feedback,
                    severity=ErrorSeverity.WARNING, # Feedback for retry
                    error_code=ErrorCode.PLAN_FEEDBACK,
                )
                response = RefinerResponse(
                    feedback=feedback,
                    errors=[warning],
                    reasoning=[{"node": self.node_name, "content": feedback}],
                )
                return {
                    "refiner_response": response,
                    "errors": [warning],
                    "reasoning": response.reasoning,
                }
            except Exception as e:
                    raise e
                     
        except Exception as e:
            logger.error(f"Node {self.node_name} failed: {e}")
            error = PipelineError(
                node=self.node_name,
                message=f"Refiner failed: {e}",
                severity=ErrorSeverity.ERROR,
                error_code=ErrorCode.REFINER_FAILED,
                stack_trace=str(e),
            )
            return {
                "refiner_response": RefinerResponse(errors=[error]),
                "reasoning": [{"node": self.node_name, "content": f"Refiner failed: {e}", "type": "error"}],
                "errors": [error],
            }
