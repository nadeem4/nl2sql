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

        allowed_tables = ""
        allowed_columns: Dict[str, Any] = {}
        fk_text = ""
        
        if state.schema_info:
            # Format tables with aliases: "table_name (alias)"
            tables_with_aliases = []
            for tbl in state.schema_info.tables:
                alias = state.schema_info.aliases.get(tbl, "")
                if alias:
                    tables_with_aliases.append(f"{tbl} ({alias})")
                else:
                    tables_with_aliases.append(tbl)
            allowed_tables = ", ".join(tables_with_aliases)
            
            allowed_columns = state.schema_info.columns
            if state.schema_info.foreign_keys:
                fk_text = f"Foreign keys: {json.dumps(state.schema_info.foreign_keys)}\n"

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
            allowed_tables=allowed_tables,
            allowed_columns=json.dumps(allowed_columns),
            fk_text=fk_text,
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
            state.plan = plan_model.model_dump()
            
        except Exception as exc:
            state.plan = None
            state.errors.append(f"Planner failed. Error: {exc}")
        return state
