"""
LangGraph wiring for the NL2SQL pipeline.

This wraps the existing node functions and GraphState dataclass into a LangGraph
StateGraph. Supply an LLM callable to enable real planning/generation.
"""
from __future__ import annotations

import dataclasses
from functools import partial
from typing import Callable, Dict, Optional

from sqlalchemy import inspect

from .capabilities import get_capabilities
from .datasource_config import DatasourceProfile
from .engine_factory import make_engine
from .nodes import intent_node, planner_node, sql_generator_node, validator_node
from .schemas import GraphState

# Type for an LLM callable: prompt -> string
LLMCallable = Callable[[str], str]


def _wrap_graphstate(fn: Callable[[GraphState], GraphState]):
    """
    LangGraph expects and returns dict-like state; wrap dataclass functions.
    """

    def wrapped(state: Dict) -> Dict:
        gs = GraphState(**state)
        gs = fn(gs)
        return dataclasses.asdict(gs)

    return wrapped


def _schema_retriever(profile: DatasourceProfile):
    def inner(state: Dict) -> Dict:
        gs = GraphState(**state)
        engine = make_engine(profile)
        inspector = inspect(engine)
        tables = inspector.get_table_names()
        gs.validation["schema_tables"] = ", ".join(sorted(tables))
        return dataclasses.asdict(gs)

    return inner


def build_graph(profile: DatasourceProfile, llm: Optional[LLMCallable] = None):
    try:
        from langgraph.graph import END, StateGraph
    except ImportError as exc:
        raise RuntimeError("langgraph is required to build the graph. Install via pip.") from exc

    # Attach capabilities marker for downstream routing
    base_state = dataclasses.asdict(GraphState(user_query=""))
    base_state["validation"]["capabilities"] = get_capabilities(profile.engine).dialect

    graph = StateGraph(dict)

    graph.add_node("intent", _wrap_graphstate(partial(intent_node, llm=llm)))
    graph.add_node("schema", _schema_retriever(profile))
    graph.add_node("planner", _wrap_graphstate(partial(planner_node, llm=llm)))
    graph.add_node(
        "sql_generator",
        _wrap_graphstate(partial(sql_generator_node, profile_engine=profile.engine, llm=llm)),
    )
    graph.add_node("validator", _wrap_graphstate(validator_node))

    graph.set_entry_point("intent")
    graph.add_edge("intent", "schema")
    graph.add_edge("schema", "planner")
    graph.add_edge("planner", "sql_generator")
    graph.add_edge("sql_generator", "validator")
    graph.add_edge("validator", END)

    return graph.compile()


def run_with_graph(profile: DatasourceProfile, user_query: str, llm: Optional[LLMCallable] = None) -> Dict:
    g = build_graph(profile, llm=llm)
    initial_state = dataclasses.asdict(
        GraphState(
            user_query=user_query,
            validation={"capabilities": get_capabilities(profile.engine).dialect},
        )
    )
    result = g.invoke(initial_state)
    return result
