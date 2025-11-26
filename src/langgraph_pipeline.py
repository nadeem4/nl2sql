"""
LangGraph wiring for the NL2SQL pipeline.

This wraps the existing node functions and GraphState dataclass into a LangGraph
StateGraph. Supply an LLM callable to enable real planning/generation.
"""
from __future__ import annotations

import dataclasses
import json
from functools import partial
from typing import Callable, Dict, Optional

from sqlalchemy import inspect

from capabilities import get_capabilities
from datasource_config import DatasourceProfile
from engine_factory import make_engine, run_read_query
from nodes import intent_node, planner_node, sql_generator_node, validator_node
from schemas import GraphState
from tracing import span

# Type for an LLM callable: prompt -> string
LLMCallable = Callable[[str], str]


def _wrap_graphstate(fn: Callable[[GraphState], GraphState]):
    """
    LangGraph expects and returns dict-like state; wrap dataclass functions.
    """

    def wrapped(state: Dict) -> Dict:
        gs = GraphState(**state)
        name = getattr(fn, "__name__", None)
        if not name and hasattr(fn, "func"):
            name = getattr(fn.func, "__name__", "node")
        with span(name or "node"):
            gs = fn(gs)
        return dataclasses.asdict(gs)

    return wrapped


def _schema_retriever(profile: DatasourceProfile):
    def inner(state: Dict) -> Dict:
        gs = GraphState(**state)
        with span("schema_retriever"):
            engine = make_engine(profile)
            inspector = inspect(engine)
            tables = inspector.get_table_names()
            gs.validation["schema_tables"] = ", ".join(sorted(tables))
            try:
                columns_map = {
                    table: [col["name"] for col in inspector.get_columns(table)]
                    for table in tables
                }
                gs.validation["schema_columns"] = json.dumps(columns_map)
            except Exception:
                pass
        return dataclasses.asdict(gs)

    return inner


def _executor(profile: DatasourceProfile):
    def inner(state: Dict) -> Dict:
        gs = GraphState(**state)
        if not gs.sql_draft:
            gs.errors.append("No SQL to execute.")
            return dataclasses.asdict(gs)
        engine = make_engine(profile)
        with span("executor", {"datasource.id": profile.id, "engine": profile.engine}):
            try:
                rows = run_read_query(engine, gs.sql_draft["sql"], row_limit=profile.row_limit)
                samples = []
                for row in rows[:3]:
                    try:
                        samples.append(dict(row._mapping))
                    except Exception:
                        samples.append(tuple(row))
                gs.execution = {"row_count": len(rows), "sample": samples}
            except Exception as exc:
                gs.execution = {"error": str(exc)}
                gs.errors.append(f"Execution error: {exc}")
        return dataclasses.asdict(gs)

    return inner


def build_graph(profile: DatasourceProfile, llm: Optional[LLMCallable] = None, llm_map: Optional[Dict[str, LLMCallable]] = None, execute: bool = True):
    try:
        from langgraph.graph import END, StateGraph
    except ImportError as exc:
        raise RuntimeError("langgraph is required to build the graph. Install via pip.") from exc

    # Attach capabilities marker for downstream routing
    base_state = dataclasses.asdict(GraphState(user_query=""))
    base_state["validation"]["capabilities"] = get_capabilities(profile.engine).dialect

    graph = StateGraph(dict)

    node_llm = lambda name: (llm_map or {}).get(name, llm) if llm_map else llm

    graph.add_node("intent", _wrap_graphstate(partial(intent_node, llm=node_llm("intent"))))
    graph.add_node("schema", _schema_retriever(profile))
    graph.add_node("planner", _wrap_graphstate(partial(planner_node, llm=node_llm("planner"))))
    graph.add_node(
        "sql_generator",
        _wrap_graphstate(
            partial(sql_generator_node, profile_engine=profile.engine, row_limit=profile.row_limit, llm=node_llm("generator"))
        ),
    )
    graph.add_node("validator", _wrap_graphstate(partial(validator_node, row_limit=profile.row_limit)))
    if execute:
        graph.add_node("executor", _executor(profile))

    graph.set_entry_point("intent")
    graph.add_edge("intent", "schema")
    graph.add_edge("schema", "planner")
    graph.add_edge("planner", "sql_generator")
    graph.add_edge("sql_generator", "validator")
    if execute:
        graph.add_edge("validator", "executor")
        graph.add_edge("executor", END)
    else:
        graph.add_edge("validator", END)

    return graph.compile()


def run_with_graph(profile: DatasourceProfile, user_query: str, llm: Optional[LLMCallable] = None, llm_map: Optional[Dict[str, LLMCallable]] = None, execute: bool = True) -> Dict:
    g = build_graph(profile, llm=llm, llm_map=llm_map, execute=execute)
    initial_state = dataclasses.asdict(
        GraphState(
            user_query=user_query,
            validation={"capabilities": get_capabilities(profile.engine).dialect},
        )
    )
    result = g.invoke(initial_state)
    return result
