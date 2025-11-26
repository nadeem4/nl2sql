"""
Minimal LangGraph-like skeleton using plain Python functions.
Replace stubs with real LangGraph nodes and LLM/tool calls.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from sqlalchemy import inspect

from .datasource_config import DatasourceProfile
from .engine_factory import make_engine
from .schemas import GraphState
from .capabilities import get_capabilities
from .nodes import intent_node, planner_node, sql_generator_node, validator_node


def intent_analyst(state: GraphState) -> GraphState:
    return intent_node(state, llm=None)


def schema_retriever(state: GraphState, profile: DatasourceProfile) -> GraphState:
    engine = make_engine(profile)
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    state.validation["schema_tables"] = ", ".join(sorted(tables))
    return state


def planner(state: GraphState) -> GraphState:
    return planner_node(state, llm=None)


def sql_generator(state: GraphState, draft_only: bool = False) -> GraphState:
    # draft_only unused here; retained for signature compatibility
    return sql_generator_node(state, profile_engine="sqlite", row_limit=20, llm=None)


def validator(state: GraphState) -> GraphState:
    return validator_node(state)


def run_pipeline(profile: DatasourceProfile, user_query: str, llm: Optional = None) -> GraphState:
    state = GraphState(user_query=user_query)
    # Attach capabilities in state.validation for visibility
    caps = get_capabilities(profile.engine)
    state.validation["capabilities"] = caps.dialect
    state = intent_node(state, llm=llm)
    state = schema_retriever(state, profile)
    state = planner_node(state, llm=llm)
    state = sql_generator_node(
        state, profile_engine=profile.engine, llm=llm
    )
    state = validator_node(state)
    return state


# Example node registry placeholder for future LangGraph wiring
NODE_ORDER: List[str] = [
    "intent_analyst",
    "schema_retriever",
    "planner",
    "sql_generator",
    "validator",
]
