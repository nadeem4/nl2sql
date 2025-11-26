"""
Minimal LangGraph-like skeleton using plain Python functions.
Replace stubs with real LangGraph nodes and LLM/tool calls.
"""
from __future__ import annotations

from typing import Dict

from sqlalchemy import inspect

from .datasource_config import DatasourceProfile
from .engine_factory import make_engine
from .schemas import GeneratedSQL, GraphState, Plan


def intent_analyst(state: GraphState) -> GraphState:
    # Stub: in a real system, call LLM to extract entities/filters.
    return state


def schema_retriever(state: GraphState, profile: DatasourceProfile) -> GraphState:
    engine = make_engine(profile)
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    state.validation["schema_tables"] = ", ".join(sorted(tables))
    return state


def planner(state: GraphState) -> GraphState:
    # Stub plan: select all products; real planner would be LLM-guided.
    plan: Plan = {
        "tables": [{"name": "products", "alias": "p"}],
        "joins": [],
        "filters": [],
        "group_by": [],
        "aggregates": [],
        "having": [],
        "order_by": [{"expr": "p.sku", "direction": "asc"}],
        "limit": 20,
    }
    state.plan = plan
    return state


def sql_generator(state: GraphState, draft_only: bool = False) -> GraphState:
    if not state.plan:
        state.errors.append("No plan to generate SQL from.")
        return state

    sql = "SELECT p.sku, p.name, p.category FROM products p ORDER BY p.sku ASC LIMIT 20;"
    state.sql_draft = GeneratedSQL(
        sql=sql,
        rationale="Default products listing as placeholder.",
        limit_enforced=True,
        draft_only=draft_only,
    )
    return state


def validator(state: GraphState) -> GraphState:
    if not state.sql_draft:
        state.errors.append("No SQL to validate.")
        return state
    if "limit" not in state.sql_draft["sql"].lower():
        state.errors.append("Missing LIMIT in SQL.")
    return state


def run_pipeline(profile: DatasourceProfile, user_query: str) -> GraphState:
    state = GraphState(user_query=user_query)
    state = intent_analyst(state)
    state = schema_retriever(state, profile)
    state = planner(state)
    state = sql_generator(state, draft_only=profile.feature_flags.allow_generate_writes)
    state = validator(state)
    return state
