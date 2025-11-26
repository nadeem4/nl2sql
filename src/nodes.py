from __future__ import annotations

import json
from typing import Any, Callable, Dict, Optional

from langchain_core.output_parsers import PydanticOutputParser
from pydantic import BaseModel, Field, ValidationError

from .capabilities import EngineCapabilities, get_capabilities
from .json_utils import extract_json_object, strip_code_fences
from .schemas import GeneratedSQL, GraphState, Plan

LLMCallable = Callable[[str], str]


class PlanModel(BaseModel):
    tables: list[Dict[str, Any]] = Field(default_factory=list)
    joins: list[Dict[str, Any]] = Field(default_factory=list)
    filters: list[Dict[str, Any]] = Field(default_factory=list)
    group_by: list[str] = Field(default_factory=list)
    aggregates: list[Dict[str, Any]] = Field(default_factory=list)
    having: list[Dict[str, Any]] = Field(default_factory=list)
    order_by: list[Dict[str, Any]] = Field(default_factory=list)
    limit: Optional[int] = None

    class Config:
        extra = "forbid"


class SQLModel(BaseModel):
    sql: str
    rationale: Optional[str] = None
    limit_enforced: Optional[bool] = None
    draft_only: Optional[bool] = None

    class Config:
        extra = "forbid"


def intent_node(state: GraphState, llm: Optional[LLMCallable] = None) -> GraphState:
    """
    Optional intent parsing. If no LLM provided, pass-through.
    """
    if not llm:
        state.validation["intent_stub"] = "No-op intent analysis"
        return state

    prompt = (
        "You are an intent analyst. Extract key entities and filters from the user query. "
        "Respond as JSON with fields: entities (list of strings), filters (list of strings), "
        "clarifications (list of questions if needed). "
        f"User query: {state.user_query}"
    )
    raw = llm(prompt)
    parsed = extract_json_object(raw)
    state.validation["intent"] = json.dumps(parsed)
    return state


def planner_node(state: GraphState, llm: Optional[LLMCallable] = None) -> GraphState:
    """
    Generates a structured plan. Falls back to a simple products listing if no LLM.
    """
    if not llm:
        state.errors.append("Planner LLM not provided; no plan generated.")
        return state

    parser = PydanticOutputParser(pydantic_object=PlanModel)
    prompt = (
        "You are a SQL planner. Return ONLY a JSON object matching this schema:\n"
        f"{parser.get_format_instructions()}\n"
        "Fill tables, joins, filters, group_by, aggregates, having, order_by, limit based on the user query. "
        "Do not include extra fields; only those defined in the schema.\n"
        f'User query: "{state.user_query}"'
    )
    raw = llm(prompt)
    raw_str = raw.strip() if isinstance(raw, str) else str(raw)
    state.validation["planner_raw"] = raw_str
    try:
        plan_model = parser.parse(strip_code_fences(raw_str))
        state.plan = plan_model.dict()
    except ValidationError as exc:
        state.plan = None
        state.errors.append(f"Planner parse failed. Error: {exc}")
    return state


def sql_generator_node(
    state: GraphState,
    profile_engine: str,
    row_limit: int,
    llm: Optional[LLMCallable] = None,
) -> GraphState:
    """
    Generate SQL from plan with dialect awareness. Falls back if no LLM.
    """
    caps: EngineCapabilities = get_capabilities(profile_engine)
    if not state.plan:
        state.errors.append("No plan to generate SQL from.")
        return state

    # Enforce limit cap from profile
    if "limit" in state.plan:
        try:
            state.plan["limit"] = min(int(state.plan["limit"]), row_limit)
        except Exception:
            state.plan["limit"] = row_limit
    else:
        state.plan["limit"] = row_limit

    if not llm:
        state.errors.append("SQL generator LLM not provided; no SQL generated.")
        return state

    limit_guidance = {
        "limit": "append 'LIMIT {n}'",
        "top_fetch": "use 'SELECT TOP {n}' or 'OFFSET/FETCH' as appropriate",
    }.get(caps.limit_syntax, "append a safe LIMIT")

    parser = PydanticOutputParser(pydantic_object=SQLModel)
    prompt = (
        "You are a SQL generator. Given a JSON plan and engine dialect, return ONLY a JSON object matching:\n"
        f"{parser.get_format_instructions()}\n"
        "Rules: avoid DDL/DML; parameterize literals where possible; quote identifiers using engine rules; "
        f"{limit_guidance}; include ORDER BY if provided; avoid SELECT * (project explicit columns). "
        "Do not wrap in code fences.\n"
        f"Engine dialect: {caps.dialect}. Plan JSON:\n{json.dumps(state.plan)}"
    )
    raw = llm(prompt)
    raw_str = raw.strip() if isinstance(raw, str) else str(raw)
    try:
        sql_model = parser.parse(strip_code_fences(raw_str))
        sql = sql_model.sql
        if "select *" in sql.lower():
            # Attempt to expand columns if schema columns are available and a single table is used
            columns_json = state.validation.get("schema_columns")
            if columns_json and state.plan and len(state.plan.get("tables", [])) == 1:
                try:
                    columns_map = json.loads(columns_json)
                    table_name = state.plan["tables"][0].get("name")
                    alias = state.plan["tables"][0].get("alias")
                    cols = columns_map.get(table_name, [])
                    quote = caps.identifier_quote or '"'
                    col_exprs = []
                    for col in cols:
                        if alias:
                            col_exprs.append(f'{alias}.{quote}{col}{quote}')
                        else:
                            col_exprs.append(f'{quote}{table_name}{quote}.{quote}{col}{quote}')
                    if col_exprs:
                        table_clause = f'{quote}{table_name}{quote}'
                        if alias:
                            table_clause += f" AS {alias}"
                        order_clause = ""
                        if state.plan.get("order_by"):
                            ob = state.plan["order_by"][0]
                            order_clause = f' ORDER BY {ob.get("expr")} {ob.get("direction","asc").upper()}'
                        sql = f"SELECT {', '.join(col_exprs)} FROM {table_clause}{order_clause} LIMIT {state.plan.get('limit', row_limit)}"
                    else:
                        state.errors.append("SELECT * rejected and no columns available to expand.")
                        state.sql_draft = None
                        return state
                except Exception:
                    state.errors.append("SELECT * rejected and column expansion failed.")
                    state.sql_draft = None
                    return state
            else:
                state.errors.append("SQL uses SELECT *; rejected.")
                state.sql_draft = None
                return state
        state.sql_draft = GeneratedSQL(
            sql=sql,
            rationale=sql_model.rationale or "LLM-generated SQL",
            limit_enforced=bool(sql_model.limit_enforced or ("limit" in sql.lower()) or (" top " in sql.lower()) or (" fetch " in sql.lower())),
            draft_only=bool(sql_model.draft_only) if sql_model.draft_only is not None else False,
        )
    except ValidationError as exc:
        state.sql_draft = None
        state.errors.append(f"SQL generation parse failed: {exc}")
    return state


def validator_node(state: GraphState) -> GraphState:
    """
    Lightweight validation: ensure SQL exists and has a limit; block write verbs.
    """
    if not state.sql_draft:
        state.errors.append("No SQL to validate.")
        return state
    sql_lower = state.sql_draft["sql"].lower()
    if any(term in sql_lower for term in ["insert ", "update ", "delete ", "drop ", "alter "]):
        state.errors.append("Write/DML detected; blocked.")
    if "limit" not in sql_lower:
        state.errors.append("Missing LIMIT in SQL.")
    return state
