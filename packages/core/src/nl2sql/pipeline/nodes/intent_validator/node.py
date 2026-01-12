from typing import Dict, Any, Optional
from langchain_core.runnables import Runnable
from langchain_core.prompts import ChatPromptTemplate

from nl2sql.pipeline.state import GraphState
from nl2sql.common.errors import PipelineError, ErrorSeverity, ErrorCode
from nl2sql.common.logger import get_logger
from .schemas import IntentValidationResult
from .prompts import INTENT_VALIDATOR_PROMPT

logger = get_logger("intent_validator")

class IntentValidatorNode:
    """Validates user intent before processing to prevent Logic Injection and Jailbreaks.

    Attributes:
        llm (Optional[Runnable]): The LLM chain used for validation.
    """

    def __init__(self, llm: Optional[Runnable]):
        """Initializes the IntentValidatorNode.

        Args:
            llm (Optional[Runnable]): The LLM runnable to use.
        """
        self.llm = llm
        if self.llm:
            prompt = ChatPromptTemplate.from_template(INTENT_VALIDATOR_PROMPT)
            self.chain = prompt | self.llm

    def __call__(self, state: GraphState) -> Dict[str, Any]:
        """Executes the intent validation logic.

        Args:
            state (GraphState): The current graph state containing the user query.

        Returns:
            Dict[str, Any]: Updates to the state, including errors if unsafe.
        """
        node_name = "intent_validator"
        query = state.user_query

        if not self.llm:
            return {
                "errors": [
                    PipelineError(
                        node=node_name,
                        message="Intent Validator LLM not configured.",
                        severity=ErrorSeverity.WARNING,
                        error_code=ErrorCode.MISSING_LLM
                    )
                ]
            }

        try:
            result: IntentValidationResult = self.chain.invoke({"user_query": query})
            
            if not result.is_safe:
                logger.warning(f"Security Violation detected: {result.violation_category} - {result.reasoning}")
                return {
                    "errors": [
                        PipelineError(
                            node=node_name,
                            message=f"Security Violation: {result.reasoning}",
                            severity=ErrorSeverity.CRITICAL,
                            error_code=ErrorCode.INTENT_VIOLATION,
                            details={"category": result.violation_category}
                        )
                    ],
                    "reasoning": [{"node": node_name, "content": f"BLOCKED: {result.violation_category}"}]
                }

            return {
                "reasoning": [{"node": node_name, "content": f"SAFE. {result.reasoning}"}]
            }

        except Exception as e:
            logger.error(f"Intent Validator failed: {e}")
            # Fail closed for high security simulation.
            return {
                "errors": [
                    PipelineError(
                        node=node_name,
                        message=f"Intent Validation Failed: {e}",
                        severity=ErrorSeverity.ERROR,
                        error_code=ErrorCode.UNKNOWN_ERROR
                    )
                ]
            }
