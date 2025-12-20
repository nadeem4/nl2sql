from __future__ import annotations
from typing import Dict, Any, Callable, Union, TYPE_CHECKING
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable

if TYPE_CHECKING:
    from nl2sql.schemas import GraphState

from .schemas import IntentResponse
from .prompts import INTENT_PROMPT
from nl2sql.logger import get_logger
from nl2sql.errors import PipelineError, ErrorSeverity, ErrorCode

logger = get_logger("intent")

LLMCallable = Union[Callable[[str], Any], Runnable]

class IntentNode:
    """Node responsible for Intent Classification, Canonicalization, and Enrichment.

    This node uses an LLM to analyze the user's natural language query, standardized it,
    extract key entities, and determine the optimal response format (Tabular, KPI, or Summary).

    Attributes:
        llm: The language model callable used for intent classification.
        prompt: The prompt template used for the classification task.
        chain: The LangChain runnable sequence.
    """

    def __init__(self, intent_llm: LLMCallable):
        """Initializes the IntentNode.

        Args:
            intent_llm: The language model callable.
        """
        self.llm = intent_llm
        self.prompt = ChatPromptTemplate.from_template(INTENT_PROMPT)
        self.chain = self.prompt | self.llm.with_structured_output(IntentResponse)

    def __call__(self, state: GraphState) -> Dict[str, Any]:
        """Executes the intent classification logic.

        Args:
            state: The current graph state containing the user query.

        Returns:
            A dictionary containing updates to the graph state:
            - user_query: The canonicalized query string.
            - response_type: The determined response format.
            - enriched_terms: List of extracted keywords and entities.
            - reasoning: Log of the classification decision.
        """
        user_query = state.user_query
        node_name = "intent"

        try:
            logger.info(f"Analyzing intent for query: {user_query}")

            response: IntentResponse = self.chain.invoke({
                "user_query": user_query
            })

            logger.info(f"Intent Classified: Type={response.response_type}, Canonical={response.canonical_query}")

            return {
                "user_query": response.canonical_query,
                "response_type": response.response_type,
                "enriched_terms": response.keywords + response.entities + response.synonyms,
                "reasoning": [{"node": "intent", "content": f"Classified as {response.response_type}. Canonical: {response.canonical_query}"}]
            }

        except Exception as e:
            logger.error(f"Node {node_name} failed: {e}")
            return {
                "response_type": "tabular",
                "enriched_terms": [],
                "reasoning": [{"node": "intent", "content": f"Intent classification failed: {str(e)}. Defaulting to tabular.", "type": "error"}],
                "errors": [
                    PipelineError(
                        node=node_name,
                        message=f"Intent classification failed: {str(e)}",
                        severity=ErrorSeverity.WARNING,
                        error_code=ErrorCode.UNKNOWN_ERROR,
                        stack_trace=str(e)
                    )
                ]
            }
