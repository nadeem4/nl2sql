from __future__ import annotations

from typing import Callable, Optional

from nl2sql.schemas import GraphState
from nl2sql.json_utils import extract_json_object, strip_code_fences
import json


from langchain_core.output_parsers import PydanticOutputParser
from nl2sql.schemas import GraphState, IntentModel

class IntentNode:
    """
    Intent analysis using Pydantic for structured output.
    """

    def __init__(self, llm: Optional[Callable[[str], str]] = None):
        self.llm = llm

    def __call__(self, state: GraphState) -> GraphState:
        if not self.llm:
            state.validation["intent_stub"] = "No-op intent analysis"
            return state
            
        parser = PydanticOutputParser(pydantic_object=IntentModel)
        prompt = (
            "You are an intent analyst. Extract key entities, filters, and technical keywords from the user query. "
            "Return ONLY a JSON object matching the following schema:\n"
            f"{parser.get_format_instructions()}\n"
            f"User query: {state.user_query}"
        )
        
        try:
            raw = self.llm(prompt)
            # Handle potential string wrapping if LLM returns it
            raw_str = raw.strip() if isinstance(raw, str) else str(raw)
            parsed = parser.parse(strip_code_fences(raw_str))
            state.validation["intent"] = parsed.model_dump()
        except Exception as e:
            # Fallback or log error
            state.errors.append(f"Intent analysis failed: {e}")
            
        return state
