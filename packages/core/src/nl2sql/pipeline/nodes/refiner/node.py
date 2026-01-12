from __future__ import annotations

import json
from typing import Callable, Optional, Union, Dict, Any, TYPE_CHECKING
from langchain_core.runnables import Runnable

if TYPE_CHECKING:
    from nl2sql.pipeline.state import GraphState
from .prompts import REFINER_PROMPT
from nl2sql.common.errors import PipelineError, ErrorSeverity, ErrorCode

from nl2sql.common.logger import get_logger

logger = get_logger("refiner")

LLMCallable = Union[Callable[[str], str], Runnable]

from langchain_core.prompts import ChatPromptTemplate

from langchain_core.output_parsers import StrOutputParser

class RefinerNode:
    """
    Analyzes validation errors and generates constructive feedback for the Planner.

    Uses an LLM to look at the failed plan, the schema, and the errors to suggest fixes.
    """

    def __init__(self, llm: Optional[LLMCallable] = None):
        """
        Initializes the RefinerNode.

        Args:
            llm: The language model to use for refinement.
        """
        self.llm = llm
        if self.llm:
            self.prompt = ChatPromptTemplate.from_template(REFINER_PROMPT)
            self.chain = self.prompt | self.llm | StrOutputParser()

    def __call__(self, state: GraphState) -> Dict[str, Any]:
        """
        Executes the summarization step.

        Args:
            state: The current graph state.

        Returns:
            Dictionary updates for the graph state with refined error messages (feedback).
        """
        node_name = "refiner"

        try:
            if not self.llm:
                return {
                    "errors": [
                        PipelineError(
                            node=node_name,
                            message="Refiner LLM not provided.",
                            severity=ErrorSeverity.CRITICAL,
                            error_code=ErrorCode.MISSING_LLM
                        )
                    ]
                }

            relevant_tables = ""
            if state.relevant_tables:
                lines = []
                for tbl in state.relevant_tables:
                    lines.append(tbl.model_dump_json(indent=2))
                    lines.append("---")
                relevant_tables = "\n".join(lines)

            failed_plan_str = "No plan generated."
            if state.plan:
                try:
                    failed_plan_str = json.dumps(state.plan, indent=2)
                except:
                    failed_plan_str = str(state.plan)

            # Extract messages from PipelineError objects
            errors_str = "\n".join(f"- {e.message}" for e in state.errors)

            reasoning_str = "No reasoning history."
            if state.reasoning:
                reasoning_str = "\n".join(
                    f"[{r.get('node', 'unknown')}]: {r.get('content')}" for r in state.reasoning
                )

            try:
                feedback = self.chain.invoke({
                    "user_query": state.user_query,
                    "relevant_tables": relevant_tables,
                    "failed_plan": failed_plan_str,
                    "errors": errors_str,
                    "reasoning": reasoning_str
                })
                
                return {
                    "errors": [
                        PipelineError(
                            node=node_name,
                            message=feedback,
                            severity=ErrorSeverity.WARNING, # Feedback for retry
                            error_code=ErrorCode.PLAN_FEEDBACK
                        )
                    ],
                    "reasoning": [{"node": "refiner", "content": feedback}]
                }
            except Exception as e:
                    raise e
                     
        except Exception as e:
            logger.error(f"Node {node_name} failed: {e}")
            return {
                "reasoning": [{"node": "refiner", "content": f"Refiner failed: {e}", "type": "error"}],
                "errors": [
                    PipelineError(
                        node=node_name,
                        message=f"Refiner failed: {e}",
                        severity=ErrorSeverity.ERROR,
                        error_code=ErrorCode.REFINER_FAILED,
                        stack_trace=str(e)
                    )
                ]
            }
