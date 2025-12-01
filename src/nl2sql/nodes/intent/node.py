from __future__ import annotations

from typing import Callable, Optional, Union

from langchain_core.runnables import Runnable

from nl2sql.schemas import GraphState, IntentModel
from nl2sql.nodes.intent.prompts import INTENT_PROMPT

LLMCallable = Union[Callable[[str], str], Runnable]


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

    def __call__(self, state: GraphState) -> GraphState:
        """
        Executes the intent analysis step.

        Args:
            state: The current graph state.

        Returns:
            The updated graph state with extracted intent information.
        """
        if not self.llm:
            state.validation["intent_stub"] = "No-op intent analysis"
            return state
            
        # No format instructions needed for structured output
        prompt = INTENT_PROMPT.format(user_query=state.user_query)
        
        try:
            # LLM returns IntentModel directly
            if isinstance(self.llm, Runnable):
                intent_model = self.llm.invoke(prompt)
            else:
                intent_model = self.llm(prompt)
            # Store raw output for debugging
            state.validation["intent"] = intent_model.model_dump_json()
            
            # Populate thoughts
            if "intent" not in state.thoughts:
                state.thoughts["intent"] = []
            
            reasoning = intent_model.reasoning or "No reasoning provided."
            state.thoughts["intent"].append(f"Reasoning: {reasoning}")
            state.thoughts["intent"].append(f"Classification: {intent_model.query_type}")
            state.thoughts["intent"].append(f"Keywords: {', '.join(intent_model.keywords)}")
            if intent_model.query_expansion:
                state.thoughts["intent"].append(f"Expansion: {', '.join(intent_model.query_expansion)}")
                
        except Exception as exc:
            state.errors.append(f"Intent extraction failed: {exc}")
            
        return state
