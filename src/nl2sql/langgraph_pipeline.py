from typing import Callable, Dict, Optional

from sqlalchemy import inspect
import json

from nl2sql.capabilities import get_capabilities
from nl2sql.datasource_config import DatasourceProfile
from nl2sql.engine_factory import make_engine, run_read_query
from nl2sql.nodes.intent import IntentNode
from nl2sql.nodes.planner import PlannerNode
from nl2sql.nodes.generator import GeneratorNode
from nl2sql.nodes.validator_node import ValidatorNode
from nl2sql.nodes.schema_node import SchemaNode
from nl2sql.schemas import GraphState
from nl2sql.tracing import span
from nl2sql.vector_store import SchemaVectorStore
from langchain_core.runnables import Runnable
from typing import Callable, Dict, Optional, Union
import dataclasses

# Type for an LLM callable: prompt -> string
LLMCallable = Union[Callable[[str], str], Runnable]



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


def build_graph(profile: DatasourceProfile, llm: Optional[LLMCallable] = None, llm_map: Optional[Dict[str, LLMCallable]] = None, execute: bool = True, vector_store: Optional[SchemaVectorStore] = None):
    try:
        from langgraph.graph import END, StateGraph
    except ImportError as exc:
        raise RuntimeError("langgraph is required to build the graph. Install via pip.") from exc

    # Attach capabilities marker for downstream routing
    base_state = dataclasses.asdict(GraphState(user_query=""))
    base_state["validation"]["capabilities"] = get_capabilities(profile.engine).dialect

    graph = StateGraph(dict)

    node_llm = lambda name: (llm_map or {}).get(name, llm) if llm_map else llm

    intent = IntentNode(llm=node_llm("intent"))
    schema_node = SchemaNode(profile=profile, vector_store=vector_store)
    planner = PlannerNode(llm=node_llm("planner"))
    generator = GeneratorNode(profile_engine=profile.engine, row_limit=profile.row_limit, llm=node_llm("generator"))
    validator = ValidatorNode(row_limit=profile.row_limit)

    def retry_node(state: Dict) -> Dict:
        gs = GraphState(**state)
        gs.retry_count += 1
        return dataclasses.asdict(gs)

    def planner_retry_node(state: Dict) -> Dict:
        gs = GraphState(**state)
        gs.retry_count += 1
        # Clear errors to give Planner a fresh start
        gs.errors = []
        return dataclasses.asdict(gs)

    def check_planner(state: Dict) -> str:
        gs = GraphState(**state)
        # If no plan or planner errors, retry
        if not gs.plan or any("Planner" in e for e in gs.errors):
            if gs.retry_count < 3:
                return "retry"
            else:
                return "end"
        return "ok"

    def check_validation(state: Dict) -> str:
        gs = GraphState(**state)
        if gs.errors:
            if gs.retry_count < 3:
                return "retry"
            else:
                return "end"
        return "ok"

    graph.add_node("intent", _wrap_graphstate(intent))
    graph.add_node("schema", _wrap_graphstate(schema_node))
    graph.add_node("planner", _wrap_graphstate(planner))
    graph.add_node("planner_retry", planner_retry_node)
    graph.add_node("sql_generator", _wrap_graphstate(generator))
    graph.add_node("validator", _wrap_graphstate(validator))
    graph.add_node("retry_handler", retry_node)
    
    if execute:
        graph.add_node("executor", _executor(profile))

    graph.set_entry_point("intent")
    graph.add_edge("intent", "schema")
    graph.add_edge("schema", "planner")
    
    graph.add_conditional_edges(
        "planner",
        check_planner,
        {
            "ok": "sql_generator",
            "retry": "planner_retry",
            "end": END
        }
    )
    graph.add_edge("planner_retry", "planner")

    graph.add_edge("sql_generator", "validator")
    
    graph.add_conditional_edges(
        "validator",
        check_validation,
        {
            "retry": "retry_handler",
            "ok": "executor" if execute else END,
            "end": END
        }
    )
    graph.add_edge("retry_handler", "sql_generator")
    
    if execute:
        graph.add_edge("executor", END)

    return graph.compile()


def run_with_graph(profile: DatasourceProfile, user_query: str, llm: Optional[LLMCallable] = None, llm_map: Optional[Dict[str, LLMCallable]] = None, execute: bool = True, vector_store: Optional[SchemaVectorStore] = None, debug: bool = False) -> Dict:
    g = build_graph(profile, llm=llm, llm_map=llm_map, execute=execute, vector_store=vector_store)
    initial_state = dataclasses.asdict(
        GraphState(
            user_query=user_query,
            validation={"capabilities": get_capabilities(profile.engine).dialect},
        )
    )
    
    if debug:
        print("\n--- Starting Graph Execution (Debug Mode) ---")
        final_state = initial_state
        for step in g.stream(initial_state):
            for node_name, state_update in step.items():
                print(f"\n--- Node: {node_name} ---")
                # Print the full state update (delta) from the node
                print(json.dumps(state_update, indent=2, default=str))
                
                # Update final state
                final_state.update(state_update)
        print("\n--- Graph Execution Complete ---\n")
        return final_state
    else:
        result = g.invoke(initial_state)
        return result
