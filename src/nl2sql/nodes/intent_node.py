from __future__ import annotations

from typing import Callable, Optional

from nl2sql.schemas import GraphState
from nl2sql.json_utils import extract_json_object


class IntentNode:
    """
    Thin wrapper around the intent agent to fit class-based node usage.
    """

    def __init__(self, llm: Optional[Callable[[str], str]] = None):
        self.llm = llm

    def __call__(self, state: GraphState) -> GraphState:
        if not self.llm:
            state.validation["intent_stub"] = "No-op intent analysis"
            return state
        prompt = (
            "You are an intent analyst. Extract key entities and filters from the user query. "
            "Respond as JSON with fields: entities (list of strings), filters (list of strings), "
            "clarifications (list of questions if needed). "
            f"User query: {state.user_query}"
        )
        raw = self.llm(prompt)
        parsed = extract_json_object(raw)
        state.validation["intent"] = json.dumps(parsed)
        return state
