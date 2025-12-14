from __future__ import annotations


from typing import Callable, Optional, Union, TYPE_CHECKING, Dict, Any

from langchain_core.runnables import Runnable

if TYPE_CHECKING:
    from nl2sql.schemas import GraphState

from .schemas import IntentModel
from nl2sql.nodes.intent.prompts import INTENT_PROMPT
from nl2sql.logger import get_logger

logger = get_logger("intent")

LLMCallable = Union[Callable[[str], str], Runnable]


from langchain_core.prompts import ChatPromptTemplate

class IntentNode:
    """
    Analyzes the user's natural language query to extract intent, keywords, and entities.

    Uses an LLM to produce a structured `IntentModel` which guides downstream schema retrieval and planning.
    """

    def __init__(self, llm: Optional[LLMCallable] = None):
        """
        Initializes the IntentNode.

        Args:
            llm: The language model to use for intent analysis.
        """
        self.llm = llm
        if self.llm:
            self.prompt = ChatPromptTemplate.from_template(INTENT_PROMPT)
            self.chain = self.prompt | self.llm

    def __call__(self, state: GraphState) -> Dict[str, Any]:
        """
        Executes the intent analysis step.

        Args:
            state: The current graph state.

        Returns:
            Dictionary updates for the graph state with extracted intent information.
        """
        node_name = "intent"

        try:
            if not self.llm:
                return {"validation": {"intent_stub": "No-op intent analysis"}}
                
            intent_model = self.chain.invoke({"user_query": state.user_query})
            
            reasoning = intent_model.reasoning or "No reasoning provided."
            
            intent_thoughts = [
                f"Reasoning: {reasoning}",
                f"Classification: {intent_model.query_type}",
                f"Keywords: {', '.join(intent_model.keywords)}"
            ]
            
            if intent_model.query_expansion:
                intent_thoughts.append(f"Expansion: {', '.join(intent_model.query_expansion)}")
            
            return {
                "intent": intent_model,
                "thoughts": {"intent": intent_thoughts}
            }
                
        except Exception as exc:
            return {"errors": [f"Intent extraction failed: {exc}"]}
