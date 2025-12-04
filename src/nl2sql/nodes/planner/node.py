from __future__ import annotations

import json
from typing import Any, Callable, Dict, Optional, Union

from langchain_core.runnables import Runnable

from nl2sql.schemas import GraphState, PlanModel
from nl2sql.nodes.planner.prompts import PLANNER_PROMPT, PLANNER_EXAMPLES

LLMCallable = Union[Callable[[str], str], Runnable]


from langchain_core.prompts import ChatPromptTemplate

class PlannerNode:
    """
    Generates a high-level execution plan from the user query.

    Uses an LLM to interpret the user's intent and map it to the database schema,
    producing a structured `PlanModel`.
    """

    def __init__(self, llm: Optional[LLMCallable] = None):
        """
        Initializes the PlannerNode.

        Args:
            llm: The language model to use for planning.
        """
        self.llm = llm
        if self.llm:
            self.prompt = ChatPromptTemplate.from_template(PLANNER_PROMPT)
            self.chain = self.prompt | self.llm

    def __call__(self, state: GraphState) -> GraphState:
        """
        Executes the planning step.

        Args:
            state: The current graph state.

        Returns:
            The updated graph state with the generated plan.
        """
        if not self.llm:
            state.errors.append("Planner LLM not provided; no plan generated.")
            return state

        schema_context = ""
        if state.schema_info:
            schema_context = state.schema_info.model_dump_json(indent=2)

        intent_context = ""
        if state.intent:
            intent_context = f"{state.intent.model_dump_json(indent=2)}\n"

        feedback = ""
        if state.errors:
            feedback = f"The previous plan was invalid. Fix the following errors:\n{json.dumps(state.errors, indent=2)}\n"
            state.errors = []

        try:
            plan_model = self.chain.invoke({
                "schema_context": schema_context,
                "intent_context": intent_context,
                "examples": PLANNER_EXAMPLES,
                "feedback": feedback,
                "user_query": state.user_query
            })
            


            plan_dump = plan_model.model_dump()
            
            if state.intent and state.intent.query_type:
                plan_dump["query_type"] = state.intent.query_type
            
            state.plan = plan_dump
            
            if "planner" not in state.thoughts:
                state.thoughts["planner"] = []
            
            reasoning = plan_model.reasoning or "No reasoning provided."
            state.thoughts["planner"].append(f"Reasoning: {reasoning}")
            state.thoughts["planner"].append(f"Query Type: {plan_model.query_type}")
            state.thoughts["planner"].append(f"Tables: {', '.join([t.name for t in plan_model.tables])}")
            
        except Exception as exc:
            state.plan = None
            state.errors.append(f"Planner failed. Error: {repr(exc)}")
        return state
