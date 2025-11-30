from __future__ import annotations

import json
from typing import Any, Callable, Dict, Optional, Union

from langchain_core.runnables import Runnable

from nl2sql.schemas import GraphState, PlanModel
from nl2sql.nodes.planner.prompts import PLANNER_PROMPT, PLANNER_EXAMPLES

LLMCallable = Union[Callable[[str], str], Runnable]


class PlannerNode:
    def __init__(self, llm: Optional[LLMCallable] = None):
        self.llm = llm

    def __call__(self, state: GraphState) -> GraphState:
        if not self.llm:
            state.errors.append("Planner LLM not provided; no plan generated.")
            return state

        schema_context = ""
        if state.schema_info:
            lines = []
            for tbl in state.schema_info.tables:
                # tbl is TableInfo
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
                    
                # Use json.dumps to ensure safe string representation
                intent_context = f"Extracted Intent: {json.dumps(intent_data)}\n"
            except Exception:
                pass

        # Handle feedback from previous validation
        feedback = ""
        if state.errors:
            feedback = f"[FEEDBACK]\nThe previous plan was invalid. Fix the following errors:\n"
            for err in state.errors:
                feedback += f"- {err}\n"
            # Clear errors after consuming them for feedback
            state.errors = []

        # No format instructions needed
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
            
            # For debugging/logging
            state.validation["planner_raw"] = plan_model.model_dump_json()

            # Validation is now done by ValidatorNode
            plan_dump = plan_model.model_dump()
            
            # Propagate query_type from Intent if available, otherwise trust Planner or default
            if state.validation.get("intent"):
                try:
                    intent_data = state.validation["intent"]
                    if isinstance(intent_data, str):
                        intent_data = json.loads(intent_data)
                    
                    # Force copy query_type from intent to plan to ensure consistency
                    if "query_type" in intent_data:
                        plan_dump["query_type"] = intent_data["query_type"]
                except Exception:
                    pass
            
            state.plan = plan_dump
            
        except Exception as exc:
            state.plan = None
            state.errors.append(f"Planner failed. Error: {exc}")
        return state
