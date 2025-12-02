from typing import Callable, Dict, Optional, Union
import dataclasses
import json
from sqlalchemy import inspect
from langchain_core.runnables import Runnable

from nl2sql.capabilities import get_capabilities
from nl2sql.datasource_registry import DatasourceRegistry
from nl2sql.nodes.intent.node import IntentNode
from nl2sql.nodes.planner.node import PlannerNode
from nl2sql.nodes.generator_node import GeneratorNode
from nl2sql.nodes.validator_node import ValidatorNode
from nl2sql.nodes.schema_node import SchemaNode
from nl2sql.nodes.router_node import RouterNode
from nl2sql.schemas import GraphState
from nl2sql.tracing import span
from nl2sql.vector_store import SchemaVectorStore
from nl2sql.graph_utils import wrap_graphstate
from nl2sql.nodes.executor_node import ExecutorNode
from nl2sql.llm_registry import LLMRegistry

# Type for an LLM callable: prompt -> string
LLMCallable = Union[Callable[[str], str], Runnable]


def build_graph(registry: DatasourceRegistry, llm_registry: LLMRegistry, execute: bool = True, vector_store: Optional[SchemaVectorStore] = None, vector_store_path: str = ""):
    """
    Builds the LangGraph state graph for the NL2SQL pipeline.

    Args:
        registry: Datasource registry.
        llm_registry: LLM registry.
        execute: Whether to include the execution step.
        vector_store: Optional vector store for schema retrieval.
        vector_store_path: Path to vector store for routing.

    Returns:
        Compiled StateGraph.
    """
    try:
        from langgraph.graph import END, StateGraph
    except ImportError as exc:
        raise RuntimeError("langgraph is required to build the graph. Install via pip.") from exc

    # Attach capabilities marker for downstream routing
    # NOTE: Capabilities are now dynamic based on selected datasource, so we init with empty
    base_state = dataclasses.asdict(GraphState(user_query=""))
    
    graph = StateGraph(dict)

    # Instantiate Nodes
    # Router
    router = RouterNode(llm_registry, registry, vector_store_path)
    
    # Intent
    intent = IntentNode(llm=llm_registry.intent_llm())
    
    # Schema (Dynamic)
    schema_node = SchemaNode(registry=registry, vector_store=vector_store)
    
    # Generator (Dynamic)
    generator = GeneratorNode(registry=registry)

    from nl2sql.subgraphs.planning import build_planning_subgraph
    
    # Prepare effective llm_map for subgraph
    # NOTE: We are using LLMRegistry now, so we might need to adjust how we pass LLMs to subgraph
    # For now, we'll assume the subgraph still takes a map or we pass the registry's base LLM
    # The current subgraph builder takes llm_map. Let's construct one from registry.
    effective_llm_map = {
        "planner": llm_registry.planner_llm(),
        "summarizer": llm_registry.summarizer_llm()
    }

    # Build the planning subgraph
    # NOTE: Subgraph builder takes row_limit. This is tricky because row_limit is dynamic now.
    # We might need to refactor the subgraph builder too, OR pass a default/max limit.
    # The subgraph uses row_limit for the prompt. 
    # Let's pass a safe default (e.g. 1000) or refactor. 
    # Refactoring subgraph is out of scope for this immediate step, let's pass 1000.
    # Ideally, the planner node should read row_limit from state/profile at runtime.
    # TODO: Refactor PlannerNode to be dynamic.
    planning_subgraph = build_planning_subgraph(effective_llm_map, row_limit=1000)

    graph.add_node("router", wrap_graphstate(router, "router"))
    graph.add_node("intent", wrap_graphstate(intent, "intent"))
    graph.add_node("schema", wrap_graphstate(schema_node, "schema"))
    graph.add_node("planning", planning_subgraph)
    graph.add_node("sql_generator", wrap_graphstate(generator, "sql_generator"))
    
    if execute:
        graph.add_node("executor", wrap_graphstate(ExecutorNode(registry=registry), "executor"))

    graph.set_entry_point("router")
    graph.add_edge("router", "intent")
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


def run_with_graph(registry: DatasourceRegistry, llm_registry: LLMRegistry, user_query: str, datasource_id: Optional[str] = None, execute: bool = True, vector_store: Optional[SchemaVectorStore] = None, vector_store_path: str = "", debug: bool = False, on_thought: Optional[Callable[[str, list[str]], None]] = None) -> Dict:
    """
    Runs the NL2SQL pipeline using LangGraph.

    Args:
        registry: Datasource registry.
        llm_registry: LLM registry.
        user_query: The user's natural language query.
        datasource_id: Optional ID to force a specific datasource.
        execute: Whether to execute the generated SQL.
        vector_store: Optional vector store for schema retrieval.
        vector_store_path: Path to vector store for routing.
        debug: Whether to print debug information.
        on_thought: Callback for streaming thoughts/tokens.

    Returns:
        The final state dictionary.
    """
    g = build_graph(registry, llm_registry, execute=execute, vector_store=vector_store, vector_store_path=vector_store_path)
    initial_state = dataclasses.asdict(
        GraphState(
            user_query=user_query,
            datasource_id=datasource_id,
            # Validation capabilities will be populated dynamically or we can init empty
            validation={"capabilities": "generic"}, 
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
