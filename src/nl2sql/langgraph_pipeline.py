from typing import Callable, Dict, Optional, Union, List
import uuid
import pathlib

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
from nl2sql.llm_registry import LLMRegistry
from IPython.display import Image, display


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
    decomposer = DecomposerNode(decomposer_llm, registry)
    
    aggregator_llm = llm_registry.aggregator_llm()
    aggregator = AggregatorNode(aggregator_llm)
    

    execution_subgraph, planning_subgraph = build_execution_subgraph(registry, llm_registry, vector_store, vector_store_path)

    def execution_wrapper(state: Union[Dict, GraphState]):
        import time
        start = time.perf_counter()
        result = execution_subgraph.invoke(state)
        duration = time.perf_counter() - start
        
        sql = result.get("sql_draft")
        
        execution = result.get("execution")
        row_count = execution.get("row_count") if isinstance(execution, dict) else getattr(execution, "row_count", 0)

        # Prioritize explicitly selected ID
        selected_id = result.get("selected_datasource_id")
        
        
        ds_type = "Unknown"
        if selected_id:
            try:
                profile = registry.get_profile(selected_id)
                ds_type = profile.engine
            except Exception:
                pass

        # Ensure valid Set[str] for datasource_id
        ds_ids = result.get("datasource_id")
        if selected_id:
            final_ds_ids = {selected_id}
        else:
            # Filter out None and ensure strings
            final_ds_ids = {str(d) for d in ds_ids if d is not None} if ds_ids else set()

        history_item = {
            "datasource_id": selected_id,
            "datasource_type": ds_type,
            "sub_query": result.get("user_query"),
            "sql": sql,
            "row_count": row_count,
            "reasoning": result.get("reasoning", {})
        }
        
        routing_info = result.get("routing_info", {})

        return {
            "intermediate_results": result.get("intermediate_results", []),
            "query_history": [history_item],
            "routing_info": routing_info,
            "datasource_id": final_ds_ids
        }

    # Add Nodes
    graph.add_node("decomposer", decomposer)
    graph.add_node("execution_branch", execution_wrapper)
    graph.add_node("aggregator", aggregator)

    # Edges
    graph.set_entry_point("decomposer")
    
    def continue_to_subqueries(state: GraphState):
        """
        Determines whether to fan out to parallel branches or proceed with a single branch.
        """
        sub_queries = state.sub_queries or [state.user_query]
        print(f"continue_to_subqueries called with {sub_queries}")

        ds_ids_list = sorted(list(state.datasource_id)) if state.datasource_id else []
        selected_id = state.selected_datasource_id
        if not selected_id and len(ds_ids_list) == 1:
            selected_id = ds_ids_list[0]
            
        return [Send("execution_branch", {"user_query": sq, "datasource_id": ds_ids_list, "selected_datasource_id": selected_id}) for sq in sub_queries]

    graph.add_conditional_edges(
        "decomposer",
        continue_to_subqueries,
        ["execution_branch"]
    )
    
    graph.add_edge("execution_branch", "aggregator")
    graph.add_edge("aggregator", END)

    graph = graph.compile()
    return graph, execution_subgraph, planning_subgraph

def run_with_graph(registry: DatasourceRegistry, llm_registry: LLMRegistry, user_query: str, datasource_id: Optional[str] = None, execute: bool = True, vector_store: Optional[SchemaVectorStore] = None, vector_store_path: str = "", callbacks: Optional[List] = None) -> Dict:
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
        callbacks: Optional list of LangChain callbacks.

    Returns:
        The final state dictionary.
    """
    g, execution_subgraph, planning_subgraph = build_graph(registry, llm_registry, execute=execute, vector_store=vector_store, vector_store_path=vector_store_path)
    
    ds_id_init = set()
    if datasource_id:
        ds_id_init = {datasource_id}
        
    initial_state = GraphState(
        user_query=user_query,
        datasource_id=ds_id_init,
        selected_datasource_id=datasource_id,  # Explicitly set selected ID if provided
        validation={"capabilities": "generic"}, 
    )

    initial_state_dict = initial_state.model_dump()
        
    from nl2sql.callbacks.observability import ObservabilityCallback
    
    final_callbacks = [ObservabilityCallback()]
    if callbacks:
        final_callbacks.extend(callbacks)
    
    # Run graph
    result = g.invoke(initial_state_dict, config={"callbacks": final_callbacks})
    
    return result
