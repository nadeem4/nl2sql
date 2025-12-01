from __future__ import annotations

import json
from typing import Any, Callable, Dict, Optional, Union

from langchain_core.runnables import Runnable

from nl2sql.schemas import GraphState, PlanModel
from nl2sql.nodes.planner.prompts import PLANNER_PROMPT, PLANNER_EXAMPLES

LLMCallable = Union[Callable[[str], str], Runnable]


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
            lines = []
            for tbl in state.schema_info.tables:
                lines.append(f"Table: {tbl.name} (Alias: {tbl.alias})")
                lines.append(f"  Columns: {', '.join(tbl.columns)}")
                if tbl.foreign_keys:
                    fk_strs = []
                    for fk in tbl.foreign_keys:
                        fk_strs.append(f"{fk.column} -> {fk.referred_table}.{fk.referred_column}")
                    lines.append(f"  Foreign Keys: {', '.join(fk_strs)}")
                lines.append("")
            schema_context = "\n".join(lines)

        intent_context = ""
        if state.validation.get("intent"):
            try:
                intent_data = state.validation["intent"]
                if isinstance(intent_data, str):
                    intent_data = json.loads(intent_data)
                    
                intent_context = f"Extracted Intent: {json.dumps(intent_data)}\n"
            except Exception:
                pass

        feedback = ""
        if state.errors:
            feedback = f"[FEEDBACK]\nThe previous plan was invalid. Fix the following errors:\n"
            for err in state.errors:
                feedback += f"- {err}\n"
            state.errors = []

        prompt = PLANNER_PROMPT.format(
            schema_context=schema_context,
            intent_context=intent_context,
            examples=PLANNER_EXAMPLES,
            feedback=feedback,
            user_query=state.user_query
        )
        
        try:
            if isinstance(self.llm, Runnable):
                plan_model = self.llm.invoke(prompt)
            else:
                plan_model = self.llm(prompt)
            
            state.validation["planner_raw"] = plan_model.model_dump_json()

            plan_dump = plan_model.model_dump()
            
            if state.validation.get("intent"):
                try:
                    intent_data = state.validation["intent"]
                    if isinstance(intent_data, str):
                        intent_data = json.loads(intent_data)
                    
                    if "query_type" in intent_data:
                        plan_dump["query_type"] = intent_data["query_type"]
                except Exception:
                    pass
            
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
