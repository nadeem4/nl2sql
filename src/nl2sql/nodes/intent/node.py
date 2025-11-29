from __future__ import annotations

from typing import Callable, Optional, Union

from langchain_core.runnables import Runnable

from nl2sql.schemas import GraphState, IntentModel
from nl2sql.nodes.intent.prompts import INTENT_PROMPT

LLMCallable = Union[Callable[[str], str], Runnable]

class IntentNode:
    """
    Intent analysis using Pydantic for structured output.
    """

    def __init__(self, llm: Optional[LLMCallable] = None):
        self.llm = llm

    def __call__(self, state: GraphState) -> GraphState:
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
                
            state.validation["intent"] = intent_model.model_dump()
        except Exception as exc:
            state.errors.append(f"Intent extraction failed: {exc}")
            
        return state
