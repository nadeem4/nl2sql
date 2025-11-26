from __future__ import annotations

import json
from typing import Any, Callable, Dict, Optional

from langchain_core.output_parsers import PydanticOutputParser
from pydantic import BaseModel, Field, ValidationError, ConfigDict
import sqlglot
from sqlglot import expressions as exp

from capabilities import EngineCapabilities, get_capabilities
from json_utils import extract_json_object, strip_code_fences
from schemas import GeneratedSQL, GraphState, Plan

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


class SQLModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sql: str
    rationale: Optional[str] = None
    limit_enforced: Optional[bool] = None
    draft_only: Optional[bool] = None


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
        "Prefer ORDER BY on business-friendly fields when no order is provided. Do not wrap in code fences.\n"
        f"Engine dialect: {caps.dialect}. Plan JSON:\n{json.dumps(state.plan)}"
    )
    raw = llm(prompt)
    raw_str = raw.strip() if isinstance(raw, str) else str(raw)
    try:
        sql_model = parser.parse(strip_code_fences(raw_str))
        sql = sql_model.sql
        lower_sql = sql.lower()
        if "select *" in lower_sql or ".*" in lower_sql:
            columns_json = state.validation.get("schema_columns")
            if columns_json and state.plan and len(state.plan.get("tables", [])) == 1:
                try:
                    columns_map = json.loads(columns_json)
                    table_entry = state.plan["tables"][0]
                    table_name = table_entry.get("name")
                    alias = table_entry.get("alias")
                    cols = columns_map.get(table_name, [])
                    quote = caps.identifier_quote or '"'
                    col_exprs = []
                    for col in cols:
                        if alias:
                            col_exprs.append(f'{alias}.{quote}{col}{quote}')
                        else:
                            col_exprs.append(f'{quote}{table_name}{quote}.{quote}{col}{quote}')
                    if not col_exprs:
                        state.errors.append("Wildcard rejected and no columns available to expand.")
                        state.sql_draft = None
                        return state
                    table_clause = f'{quote}{table_name}{quote}'
                    if alias:
                        table_clause += f" AS {alias}"
                    order_clause = ""
                    if state.plan.get("order_by"):
                        ob = state.plan["order_by"][0]
                        order_clause = f' ORDER BY {ob.get("expr")} {ob.get("direction","asc").upper()}'
                    sql = f"SELECT {', '.join(col_exprs)} FROM {table_clause}{order_clause} LIMIT {state.plan.get('limit', row_limit)}"
                except Exception:
                    state.errors.append("Wildcard rejected and column expansion failed.")
                    state.sql_draft = None
                    return state
            else:
                state.errors.append("Wildcard select rejected (SELECT * or table.*).")
                state.sql_draft = None
                return state
        # Enforce ORDER BY presence when plan specifies it
        if state.plan.get("order_by") and "order by" not in lower_sql:
            ob = state.plan["order_by"][0]
            dir_val = ob.get("direction", "asc").upper()
            sql = f"{sql.rstrip(';')} ORDER BY {ob.get('expr')} {dir_val}"
        # Enforce LIMIT is within row_limit
        if "limit" in lower_sql:
            try:
                # naive parse for LIMIT value
                parts = lower_sql.split("limit")
                if len(parts) > 1:
                    limit_val = parts[-1].strip().split()[0]
                    lim = int(limit_val)
                    if lim > row_limit:
                        state.errors.append(f"Limit {lim} exceeds allowed {row_limit}.")
                        state.sql_draft = None
                        return state
                else:
                    state.errors.append("Could not parse LIMIT value.")
                    state.sql_draft = None
                    return state
            except Exception:
                state.errors.append("Could not parse LIMIT value.")
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


def validator_node(state: GraphState, row_limit: int | None = None) -> GraphState:
    """
    Stricter validation: single statement, LIMIT present and <= row_limit, no DDL/DML, no UNION, no wildcards, ORDER BY honored.
    """
    if not state.sql_draft:
        state.errors.append("No SQL to validate.")
        return state
    sql_text = state.sql_draft["sql"]
    sql_lower = sql_text.lower()
    if any(term in sql_lower for term in ["insert ", "update ", "delete ", "drop ", "alter "]):
        state.errors.append("Write/DML detected; blocked.")
    if "limit" not in sql_lower:
        state.errors.append("Missing LIMIT in SQL.")
    try:
        parsed = sqlglot.parse_one(sql_text)
    except Exception:
        state.errors.append("SQL parse failed; blocked.")
        return state
    # block UNION
    if parsed.find(exp.Union):
        state.errors.append("UNION detected; blocked.")
    # enforce ORDER BY if plan has it
    if state.plan and state.plan.get("order_by"):
        has_order = bool(parsed.args.get("order"))
        if not has_order:
            state.errors.append("Plan requested ORDER BY but SQL missing it.")
    # enforce LIMIT numeric and within row_limit
    limit_val = _extract_limit(sql_text)
    if limit_val is None:
        state.errors.append("Could not parse LIMIT value.")
    elif row_limit is not None and limit_val > row_limit:
        state.errors.append(f"Limit {limit_val} exceeds allowed {row_limit}.")
    return state


def _extract_limit(sql_text: str) -> int | None:
    try:
        parsed = sqlglot.parse_one(sql_text)
    except Exception:
        return None
    limit = parsed.args.get("limit")
    if not limit:
        return None
    try:
        # limit can be a tuple (this, offset); grab the expression
        expr = limit.expression if hasattr(limit, "expression") else limit
        return int(expr.this)
    except Exception:
        return None
