from __future__ import annotations

import json
from typing import Any, Callable, Dict, Optional, Union

from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.exceptions import OutputParserException
from langchain_core.runnables import Runnable
from pydantic import ValidationError

from nl2sql.json_utils import strip_code_fences
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

        parser = PydanticOutputParser(pydantic_object=PlanModel)
        
        prompt = PLANNER_PROMPT.format(
            format_instructions=parser.get_format_instructions(),
            allowed_tables=allowed_tables,
            allowed_columns=json.dumps(allowed_columns),
            fk_text=fk_text,
            intent_context=intent_context,
            examples=PLANNER_EXAMPLES,
            user_query=state.user_query
        )
        
      
        if isinstance(self.llm, Runnable):
            raw = self.llm.invoke(prompt)
        else:
            raw = self.llm(prompt)
            
        raw_str = raw.content if hasattr(raw, "content") else (raw.strip() if isinstance(raw, str) else str(raw))
        state.validation["planner_raw"] = raw_str
        try:
            plan_model = parser.parse(strip_code_fences(raw_str))
            
            # Validate needed_columns against schema
            if state.schema_info:
                invalid_cols = []
                for col_ref in plan_model.needed_columns:
                    parts = col_ref.split(".")
                    if len(parts) == 2:
                        tbl, col = parts
                        if tbl in state.schema_info.columns:
                            if col not in state.schema_info.columns[tbl]:
                                invalid_cols.append(col_ref)
                        else:
                            # Table not in schema, technically invalid but maybe alias?
                            # For strictness, assume invalid if not in schema info
                            invalid_cols.append(col_ref)
                    else:
                        # Unqualified or weird format
                        invalid_cols.append(col_ref)
                
                if invalid_cols:
                    state.errors.append(f"Plan references unknown columns: {', '.join(invalid_cols)}")
                    state.plan = None
                    return state

            state.plan = plan_model.model_dump()
        except (ValidationError, OutputParserException) as exc:
            state.plan = None
            state.errors.append(f"Planner parse failed. Error: {exc}")
        return state
