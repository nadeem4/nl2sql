from typing import Callable, Dict, Optional, Union
import dataclasses
import json
from sqlalchemy import inspect
from langchain_core.runnables import Runnable

from nl2sql.capabilities import get_capabilities
from nl2sql.datasource_config import DatasourceProfile
from nl2sql.engine_factory import make_engine, run_read_query
from nl2sql.nodes.intent.node import IntentNode
from nl2sql.nodes.planner.node import PlannerNode
from nl2sql.nodes.generator_node import GeneratorNode
from nl2sql.nodes.validator_node import ValidatorNode
from nl2sql.nodes.schema_node import SchemaNode
from nl2sql.schemas import GraphState
from nl2sql.tracing import span
from nl2sql.vector_store import SchemaVectorStore
from nl2sql.graph_utils import wrap_graphstate
from nl2sql.nodes.executor_node import ExecutorNode

# Type for an LLM callable: prompt -> string
LLMCallable = Union[Callable[[str], str], Runnable]


def build_graph(profile: DatasourceProfile, llm: Optional[LLMCallable] = None, llm_map: Optional[Dict[str, LLMCallable]] = None, execute: bool = True, vector_store: Optional[SchemaVectorStore] = None):
    """
    Builds the LangGraph state graph for the NL2SQL pipeline.

    Args:
        profile: Database connection profile.
        llm: Default LLM callable.
        llm_map: Map of node names to specific LLM callables.
        execute: Whether to include the execution step.
        vector_store: Optional vector store for schema retrieval.

    Returns:
        Compiled StateGraph.
    """
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
    generator = GeneratorNode(profile_engine=profile.engine, row_limit=profile.row_limit)

    from nl2sql.subgraphs.planning import build_planning_subgraph
    
    # Prepare effective llm_map for subgraph
    effective_llm_map = (llm_map or {}).copy()
    if llm:
        for key in ["planner", "summarizer"]:
            if key not in effective_llm_map:
                effective_llm_map[key] = llm

    # Build the planning subgraph
    planning_subgraph = build_planning_subgraph(effective_llm_map, row_limit=profile.row_limit)

    graph.add_node("intent", wrap_graphstate(intent, "intent"))
    graph.add_node("schema", wrap_graphstate(schema_node, "schema"))
    graph.add_node("planning", planning_subgraph)
    graph.add_node("sql_generator", wrap_graphstate(generator, "sql_generator"))
    
    if execute:
        graph.add_node("executor", wrap_graphstate(ExecutorNode(profile=profile), "executor"))

    graph.set_entry_point("intent")
    graph.add_edge("intent", "schema")
    graph.add_edge("schema", "planning")
    
    def check_planning_result(state: Dict) -> str:
        gs = GraphState(**state)
        if gs.plan and not gs.errors:
            return "ok"
        return "end"

    graph.add_conditional_edges(
        "planning",
        check_planning_result,
        {
            "ok": "sql_generator",
            "end": END
        }
    )
    
    if execute:
        graph.add_edge("sql_generator", "executor")
        graph.add_edge("executor", END)
    else:
        graph.add_edge("sql_generator", END)

    return graph.compile()


def run_with_graph(profile: DatasourceProfile, user_query: str, llm: Optional[LLMCallable] = None, llm_map: Optional[Dict[str, LLMCallable]] = None, execute: bool = True, vector_store: Optional[SchemaVectorStore] = None, debug: bool = False, on_thought: Optional[Callable[[str, list[str]], None]] = None) -> Dict:
    """
    Runs the NL2SQL pipeline using LangGraph.

    Args:
        profile: Database connection profile.
        user_query: The user's natural language query.
        llm: Default LLM callable.
        llm_map: Map of node names to specific LLM callables.
        execute: Whether to execute the generated SQL.
        vector_store: Optional vector store for schema retrieval.
        debug: Whether to print debug information.
        on_thought: Callback for streaming thoughts/tokens.

    Returns:
        The final state dictionary.
    """
    g = build_graph(profile, llm=llm, llm_map=llm_map, execute=execute, vector_store=vector_store)
    initial_state = dataclasses.asdict(
        GraphState(
            user_query=user_query,
            validation={"capabilities": get_capabilities(profile.engine).dialect},
        )
    )
    
    import time
    start_total = time.perf_counter()
    
    node_map = {
        "intent": "intent",
        "schema": "schema",
        "planner": "planner",
        "validator": "validator",
        "sql_generator": "generator",
        "summarizer": "summarizer"
    }

    if debug or on_thought:
        if debug:
            print("\n--- Starting Graph Execution (Debug Mode) ---")
        
        final_state = initial_state
        for namespace, mode, payload in g.stream(initial_state, stream_mode=["updates", "messages"], subgraphs=True):
            if mode == "updates":
           
                for node_name, state_update in payload.items():
                    if debug:
                        print(f"\n--- Node: {node_name} ---")
                        print(json.dumps(state_update, indent=2, default=str))
                    
                    final_state.update(state_update)

                    if on_thought:
                        thought_key = node_map.get(node_name)
                        if thought_key:
                            thoughts = state_update.get("thoughts", {}).get(thought_key)
                            if thoughts:
                                on_thought(thought_key, thoughts)

            elif mode == "messages":
                chunk, metadata = payload
                node_name = metadata.get("langgraph_node", "")
                thought_key = node_map.get(node_name)
                
                if thought_key and on_thought:
                    if hasattr(chunk, "content") and chunk.content:
                        on_thought(thought_key, [chunk.content], token=True)
        
        if debug:
            print("\n--- Graph Execution Complete ---\n")
        result = final_state
    else:
        result = g.invoke(initial_state)
    
    total_duration = time.perf_counter() - start_total
    if "latency" not in result:
        result["latency"] = {}
    result["latency"]["total"] = total_duration
    
    return result
