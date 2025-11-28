from __future__ import annotations

from typing import Callable, Optional, Union

from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.runnables import Runnable

from nl2sql.schemas import GraphState, IntentModel
from nl2sql.json_utils import strip_code_fences
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
            
        parser = PydanticOutputParser(pydantic_object=IntentModel)
        prompt = INTENT_PROMPT.format(
            format_instructions=parser.get_format_instructions(),
            user_query=state.user_query
        )
        
        try:
            if isinstance(self.llm, Runnable):
                raw = self.llm.invoke(prompt)
            else:
                raw = self.llm(prompt)
                
            raw_str = raw.content if hasattr(raw, "content") else (raw.strip() if isinstance(raw, str) else str(raw))
            parsed = parser.parse(strip_code_fences(raw_str))
            state.validation["intent"] = parsed.model_dump()
        except Exception as e:
            # Fallback or log error
            state.errors.append(f"Intent analysis failed: {e}")
            
        return state
