from __future__ import annotations

import json
from typing import Any, Callable, Dict, Optional

from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.exceptions import OutputParserException
from pydantic import ValidationError

from nl2sql.json_utils import strip_code_fences
from nl2sql.schemas import GraphState, PlanModel


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
        prompt = (
            "You are a SQL planner. Return ONLY a JSON object matching this schema:\n"
            f"{parser.get_format_instructions()}\n"
            "Fill tables, joins, filters, group_by, aggregates, having, order_by, limit based on the user query. "
            "Do not include extra fields; only those defined in the schema. "
            "Use only the allowed tables/columns provided. If the user asks for a table/column not listed, ask for clarification or use the closest match from allowed tables.\n"
            f"Allowed tables: {allowed_tables}\n"
            f"Allowed columns by table: {json.dumps(allowed_columns)}\n"
            f"{fk_text}"
            f"{intent_context}"
            f'User query: "{state.user_query}"'
        )
        raw = self.llm(prompt)
        raw_str = raw.strip() if isinstance(raw, str) else str(raw)
        state.validation["planner_raw"] = raw_str
        try:
            plan_model = parser.parse(strip_code_fences(raw_str))
            state.plan = plan_model.model_dump()
        except (ValidationError, OutputParserException) as exc:
            state.plan = None
            state.errors.append(f"Planner parse failed. Error: {exc}")
        return state
