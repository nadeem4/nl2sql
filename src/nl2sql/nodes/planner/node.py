from __future__ import annotations

import json
import json
from typing import Any, Callable, Dict, Optional, Union, TYPE_CHECKING

from langchain_core.runnables import Runnable

if TYPE_CHECKING:
    from nl2sql.schemas import GraphState

from .schemas import PlanModel
from nl2sql.nodes.planner.prompts import PLANNER_PROMPT, PLANNER_EXAMPLES
from nl2sql.datasource_registry import DatasourceRegistry

from nl2sql.logger import get_logger

logger = get_logger("planner")

LLMCallable = Union[Callable[[str], str], Runnable]


from langchain_core.prompts import ChatPromptTemplate

class PlannerNode:
    """
    Generates a high-level execution plan from the user query.

    Uses an LLM to interpret the user's intent and map it to the database schema,
    producing a structured `PlanModel`.
    """

    def __init__(self, registry: DatasourceRegistry, llm: Optional[LLMCallable] = None):
        """
        Initializes the PlannerNode.

        Args:
            registry: Datasource registry for accessing profiles.
            llm: The language model to use for planning.
        """
        self.registry = registry
        self.llm = llm
        if self.llm:
            self.prompt = ChatPromptTemplate.from_template(PLANNER_PROMPT)
            self.chain = self.prompt | self.llm

    def __call__(self, state: GraphState) -> Dict[str, Any]:
        """
        Executes the planning step.

        Args:
            state: The current graph state.

        Returns:
            Dictionary updates for the graph state with the generated plan.
        """
        node_name = "planner"

        try:
            if not self.llm:
                return {"errors": ["Planner LLM not provided; no plan generated."]}

            schema_context = ""
            if state.schema_info:
                schema_context = state.schema_info.model_dump_json(indent=2)

            intent_context = ""
            if state.intent:
                intent_context = f"{state.intent.model_dump_json(indent=2)}\n"

            feedback = ""
            if state.errors:
                feedback = f"The previous plan was invalid. Fix the following errors:\n{json.dumps(state.errors, indent=2)}\n"
            
            # Get date format from profile
            date_format = "ISO 8601 (YYYY-MM-DD)"
            try:
                if state.selected_datasource_id:
                     profile = self.registry.get_profile(state.selected_datasource_id)
                     date_format = profile.date_format
            except Exception:
                pass
                
            plan_model = self.chain.invoke({
                "schema_context": schema_context,
                "intent_context": intent_context,
                "examples": PLANNER_EXAMPLES,
                "feedback": feedback,
                "user_query": state.user_query,
                "date_format": date_format
            })
            
            plan_dump = plan_model.model_dump()
            
            if state.intent and state.intent.query_type:
                plan_dump["query_type"] = state.intent.query_type
            
            reasoning = plan_model.reasoning or "No reasoning provided."
            planner_thoughts = [
                f"Reasoning: {reasoning}\n",
                f"Query Type: {plan_model.query_type}\n"
                f"Tables: {', '.join([t.name for t in plan_model.tables])}\n"
            ]
            
            return {
                "plan": plan_dump,
                "reasoning": [{"node": "planner", "content": planner_thoughts}],
                "errors": [] 
            }
            
        except Exception as exc:
            return {
                "plan": None,
                "errors": [f"Planner failed. Error: {repr(exc)}"]
            }
