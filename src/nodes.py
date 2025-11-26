from __future__ import annotations

import json
from typing import Any, Callable, Dict, Optional

from .capabilities import EngineCapabilities, get_capabilities
from .schemas import GeneratedSQL, GraphState, Plan

LLMCallable = Callable[[str], str]


def _safe_json_loads(payload: str) -> Dict[str, Any]:
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return {}


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
    parsed = _safe_json_loads(raw)
    state.validation["intent"] = json.dumps(parsed)
    return state


def planner_node(state: GraphState, llm: Optional[LLMCallable] = None) -> GraphState:
    """
    Generates a structured plan. Falls back to a simple products listing if no LLM.
    """
    if not llm:
        state.plan = {
            "tables": [{"name": "products", "alias": "p"}],
            "joins": [],
            "filters": [],
            "group_by": [],
            "aggregates": [],
            "having": [],
            "order_by": [{"expr": "p.sku", "direction": "asc"}],
            "limit": 20,
        }
        return state

    prompt = (
        "You are a SQL planner. Produce a JSON plan with keys: tables (name, alias), "
        "joins (left, right, on[], join_type), filters (column, op, value, logic), "
        "group_by, aggregates (expr, alias), having, order_by (expr, direction), limit. "
        f"User query: {state.user_query}"
    )
    raw = llm(prompt)
    parsed = _safe_json_loads(raw)
    state.plan = parsed or None
    if not state.plan:
        state.errors.append("Planner returned no plan")
    return state


def sql_generator_node(
    state: GraphState, profile_engine: str, llm: Optional[LLMCallable] = None
) -> GraphState:
    """
    Generate SQL from plan with dialect awareness. Falls back if no LLM.
    """
    caps: EngineCapabilities = get_capabilities(profile_engine)
    if not state.plan:
        state.errors.append("No plan to generate SQL from.")
        return state

    if not llm:
        sql = "SELECT p.sku, p.name, p.category FROM products p ORDER BY p.sku ASC LIMIT 20;"
        state.sql_draft = GeneratedSQL(
            sql=sql,
            rationale="Default products listing as placeholder.",
            limit_enforced=True,
            draft_only=False,
        )
        return state

    prompt = (
        "You are a SQL generator. Given a JSON plan and engine dialect, output SQL with a SAFE LIMIT. "
        "Use parameterization where possible and avoid DDL/DML. "
        f"Engine dialect: {caps.dialect}, limit syntax: {caps.limit_syntax}. "
        f"Plan JSON:\n{json.dumps(state.plan)}"
    )
    raw = llm(prompt)
    sql = raw.strip()
    state.sql_draft = GeneratedSQL(
        sql=sql,
        rationale="LLM-generated SQL",
        limit_enforced="limit" in sql.lower(),
        draft_only=False,
    )
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
