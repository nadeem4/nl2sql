from typing import Callable, Dict, Optional, Union, List

import json
from sqlalchemy import inspect
from langchain_core.runnables import Runnable
from langgraph.graph import END, StateGraph
from langgraph.types import Send

from nl2sql.capabilities import get_capabilities
from nl2sql.datasource_registry import DatasourceRegistry
from nl2sql.nodes.decomposer import DecomposerNode
from nl2sql.nodes.aggregator import AggregatorNode
from nl2sql.subgraphs.execution import build_execution_subgraph
from nl2sql.schemas import GraphState, DecomposerResponse, AggregatedResponse
from nl2sql.tracing import span
from nl2sql.vector_store import SchemaVectorStore
from nl2sql.graph_utils import wrap_graphstate
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
    
    graph = StateGraph(GraphState)

    decomposer_llm = llm_registry.decomposer_llm()
    decomposer = DecomposerNode(decomposer_llm)
    
    aggregator_llm = llm_registry.aggregator_llm()
    aggregator = AggregatorNode(aggregator_llm)
    

    execution_subgraph = build_execution_subgraph(registry, llm_registry, vector_store, vector_store_path)

    def execution_wrapper(state: Union[Dict, GraphState]):
        result = execution_subgraph.invoke(state)
        return {
            "intermediate_results": result.get("intermediate_results", []),
            "latency": result.get("latency", {})
        }

    # Add Nodes
    graph.add_node("decomposer", wrap_graphstate(decomposer, "decomposer"))
    graph.add_node("execution_branch", execution_wrapper)
    graph.add_node("aggregator", wrap_graphstate(aggregator, "aggregator"))

    # Edges
    graph.set_entry_point("decomposer")
    
    def continue_to_subqueries(state: GraphState):
        """
        Determines whether to fan out to parallel branches or proceed with a single branch.
        """
        sub_queries = state.sub_queries or [state.user_query]
        print(f"DEBUG: continue_to_subqueries called with {sub_queries}")
        
        # We use Send to create parallel branches for each sub-query
        # We override 'user_query' for the branch execution and propagate datasource_id
        return [Send("execution_branch", {"user_query": sq, "datasource_id": state.datasource_id}) for sq in sub_queries]

    graph.add_conditional_edges(
        "decomposer",
        continue_to_subqueries,
        ["execution_branch"]
    )
    
    graph.add_edge("execution_branch", "aggregator")
    graph.add_edge("aggregator", END)

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
    
    # Initialize GraphState
    initial_state = GraphState(
        user_query=user_query,
        datasource_id=datasource_id,
        validation={"capabilities": "generic"}, 
    )
    

    initial_state_dict = initial_state.model_dump()
    
    import time
    start_total = time.perf_counter()
    
    node_map = {
        "decomposer": "decomposer",
        "router": "router",
        "intent": "intent",
        "schema": "schema",
        "planner": "planner",
        "validator": "validator",
        "sql_generator": "generator",
        "summarizer": "summarizer",
        "aggregator": "aggregator"
    }

    if debug or on_thought:
        if debug:
            print("\n--- Starting Graph Execution (Debug Mode) ---")
        
        final_state = initial_state_dict
        # Track branch names (namespace -> datasource_id)
        branch_map = {}

        # We need to handle subgraphs in streaming
        for namespace, mode, payload in g.stream(initial_state_dict, stream_mode=["updates", "messages"], subgraphs=True):
            
            # Determine branch ID (first element of namespace usually identifies the parallel branch)
            branch_id = namespace[0] if namespace else "main"
            
            if mode == "updates":
           
                for node_name, state_update in payload.items():
                    if debug:
                        print(f"\n--- Node: {node_name} ---")
                        # print(json.dumps(state_update, indent=2, default=str)) # Can be verbose
                    
                    # Update final state (simple merge for top-level, might need care for lists)
                    if isinstance(state_update, dict):
                        final_state.update(state_update)
                        
                        # Capture datasource_id if present to name the branch
                        if "datasource_id" in state_update and state_update["datasource_id"]:
                            branch_map[branch_id] = state_update["datasource_id"]

                    if on_thought:
                        # Map node name (which might be namespaced like "execution_branch:router")
                        # We need to extract the actual node name
                        actual_node = node_name.split(":")[-1]
                        thought_key = node_map.get(actual_node)
                        
                        if thought_key:
                            thoughts = state_update.get("thoughts", {}).get(thought_key)
                            if thoughts:
                                # Append branch label if available
                                branch_label = branch_map.get(branch_id)
                                if not branch_label and branch_id.startswith("execution_branch"):
                                    # Fallback to short ID
                                    branch_label = branch_id.split(":")[-1][:4]
                                
                                display_node = f"{thought_key} ({branch_label})" if branch_label else thought_key
                                on_thought(display_node, thoughts)

            elif mode == "messages":
                chunk, metadata = payload
                node_name = metadata.get("langgraph_node", "")
                actual_node = node_name.split(":")[-1]
                thought_key = node_map.get(actual_node)
                
                if thought_key and on_thought:
                    if hasattr(chunk, "content") and chunk.content:
                        # Append branch label if available
                        branch_label = branch_map.get(branch_id)
                        if not branch_label and branch_id.startswith("execution_branch"):
                             branch_label = branch_id.split(":")[-1][:4]
                        
                        display_node = f"{thought_key} ({branch_label})" if branch_label else thought_key
                        on_thought(display_node, [chunk.content], token=True)
        
        if debug:
            print("\n--- Graph Execution Complete ---\n")
        result = final_state
    else:
        result = g.invoke(initial_state_dict)
    
    total_duration = time.perf_counter() - start_total
    if "latency" not in result:
        result["latency"] = {}
    result["latency"]["total"] = total_duration
    
    return result
