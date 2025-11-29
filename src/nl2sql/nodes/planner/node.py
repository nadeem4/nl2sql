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
            allowed_tables = ", ".join(state.schema_info.tables)
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

        # No format instructions needed
        prompt = PLANNER_PROMPT.format(
            allowed_tables=allowed_tables,
            allowed_columns=json.dumps(allowed_columns),
            fk_text=fk_text,
            intent_context=intent_context,
            examples=PLANNER_EXAMPLES,
            user_query=state.user_query
        )
        
        try:
            if isinstance(self.llm, Runnable):
                plan_model = self.llm.invoke(prompt)
            else:
                plan_model = self.llm(prompt)
            
            # For debugging/logging, we might want to store the raw plan if possible, 
            # but with structured output we get the object. We can dump it back to json.
            state.validation["planner_raw"] = plan_model.model_dump_json()

            # Validate needed_columns against schema
            if state.schema_info:
                # Build alias map
                alias_map = {}
                for t in plan_model.tables:
                    if t.alias:
                        alias_map[t.alias] = t.name
                
                invalid_cols = []
                for col_ref in plan_model.needed_columns:
                    parts = col_ref.split(".")
                    if len(parts) == 2:
                        tbl_ref, col = parts
                        # Resolve alias if present
                        real_table = alias_map.get(tbl_ref, tbl_ref)
                        
                        if real_table in state.schema_info.columns:
                            if col not in state.schema_info.columns[real_table]:
                                invalid_cols.append(col_ref)
                        else:
                            # Table not in schema
                            invalid_cols.append(col_ref)
                    else:
                        # Unqualified or weird format
                        invalid_cols.append(col_ref)
                
                if invalid_cols:
                    state.errors.append(f"Plan references unknown columns: {', '.join(invalid_cols)}")
                    state.plan = None
                    return state

            state.plan = plan_model.model_dump()
        except Exception as exc:
            state.plan = None
            state.errors.append(f"Planner failed. Error: {exc}")
        return state
