from __future__ import annotations

import json
from typing import Any, Callable, Dict, Optional

from langchain_core.output_parsers import PydanticOutputParser
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from nl2sql.json_utils import strip_code_fences
from nl2sql.schemas import GraphState, Plan

LLMCallable = Callable[[str], str]


class PlanModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tables: list[Dict[str, Any]] = Field(default_factory=list)
    joins: list[Dict[str, Any]] = Field(default_factory=list)
    filters: list[Dict[str, Any]] = Field(default_factory=list)
    group_by: list[str] = Field(default_factory=list)
    aggregates: list[Dict[str, Any]] = Field(default_factory=list)
    having: list[Dict[str, Any]] = Field(default_factory=list)
    order_by: list[Dict[str, Any]] = Field(default_factory=list)
    limit: Optional[int] = None


class PlannerNode:
    def __init__(self, llm: Optional[LLMCallable] = None):
        self.llm = llm

    def __call__(self, state: GraphState) -> GraphState:
        if not self.llm:
            state.errors.append("Planner LLM not provided; no plan generated.")
            return state

        allowed_tables = state.validation.get("schema_tables", "")
        allowed_columns: Dict[str, Any] = {}
        allowed_fks = state.validation.get("schema_fks")
        if state.validation.get("schema_columns"):
            try:
                allowed_columns = json.loads(state.validation["schema_columns"])
            except Exception:
                allowed_columns = {}
        fk_text = f"Foreign keys: {allowed_fks}\n" if allowed_fks else ""

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
            f'User query: "{state.user_query}"'
        )
        raw = self.llm(prompt)
        raw_str = raw.strip() if isinstance(raw, str) else str(raw)
        state.validation["planner_raw"] = raw_str
        try:
            plan_model = parser.parse(strip_code_fences(raw_str))
            state.plan = plan_model.dict()
        except ValidationError as exc:
            state.plan = None
            state.errors.append(f"Planner parse failed. Error: {exc}")
        return state
