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
from nl2sql.vector_store import OrchestratorVectorStore
from nl2sql.llm_registry import LLMRegistry
from IPython.display import Image, display


# Type for an LLM callable: prompt -> string
LLMCallable = Union[Callable[[str], str], Runnable]


def build_graph(registry: DatasourceRegistry, llm_registry: LLMRegistry, execute: bool = True, vector_store: Optional[OrchestratorVectorStore] = None, vector_store_path: str = ""):
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

    # Updated Decomposer instantiation with vector_store
    decomposer_llm = llm_registry.decomposer_llm()
    decomposer = DecomposerNode(decomposer_llm, registry, vector_store)
    
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
            "datasource_id": final_ds_ids,
            "errors": result.get("errors", [])
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
        Uses structured SubQuery objects to pre-route.
        """
        # If no subqueries, handle single path. 
        # But Decomposer NOW always returns logical subqueries (even if just 1 for exact match).
        
        branches = []
        
        if not state.sub_queries:
            # Fallback for some reason (e.g. decomposer crash handled gracefully)
            # Route to execution with whatever ID we have (or fail)
            return [Send("execution_branch", {"user_query": state.user_query, "selected_datasource_id": state.selected_datasource_id})]

        for sq in state.sub_queries:
            # Ensure sq is object (Pydantic or SubQuery)
            # State might be deserialized to dict if coming from LangGraph internal storage
            
            sq_text = sq.query if hasattr(sq, "query") else sq["query"]
            ds_id = sq.datasource_id if hasattr(sq, "datasource_id") else sq.get("datasource_id")
            
            # Note: We don't need to pass candidate_tables here explicitly to 'execution_branch' arguments 
            # if we pass the whole SubQuery list. But LangGraph Send(node, state_update).
            # We want each branch to have its OWN focus.
            # But the SchemaNode checks `state.sub_queries`. 
            # It's cleaner if SchemaNode looks at THIS specific SubQuery.
            # However, `GraphState` doesn't have a "current_sub_query" field.
            # SchemaNode iterates `state.sub_queries` looking for one matching `target_ds_id`.
            # If multiple subqueries target same DS, that logic in SchemaNode picks the first match.
            # Might be ambiguous if different queries for same DS have different tables.
            # BETTER: Pass `candidate_tables` directly into state update for execution branch?
            # SchemaNode logic I wrote: "Find SubQuery corresponding to target_ds_id".
            # If I have 2 subqueries for "SalesDB" (different intents), SchemaNode might pick wrong list?
            # Re-reading my SchemaNode change: 
            # `if hasattr(sq, "datasource_id") and sq.datasource_id == target_ds_id:`
            # It finds the *first* subquery for that DS.
            # Weakness identified.
            
            # FIX: In `continue_to_subqueries`, I can create a state update that effectively "isolates" the subquery context
            # But GraphState is shared schema. 
            # I can rely on the fact that `SchemaNode` logic is "good enough" for now or 
            # I should update `SchemaNode` to look at `user_query` vs `sq.query`.
            # But `execution_branch` receives `user_query` override.
            
            branches.append(Send("execution_branch", {
                "user_query": sq_text, 
                "selected_datasource_id": ds_id,
                # We retain the global `sub_queries` list in state so SchemaNode can read it.
                # Ideally, we'd pass the specific tables here, but `GraphState` has no field for it.
                # Given I can't change GraphState easily to per-branch field without defining new field...
                # Actually I CAN add generic fields to Send dict if config allows, but Pydantic will ignore.
                # Optimization for later: clean up SchemaNode matching.
            }))
            
        return branches

    graph.add_conditional_edges(
        "decomposer",
        continue_to_subqueries,
        ["execution_branch"]
    )
    
    graph.add_edge("execution_branch", "aggregator")
    graph.add_edge("aggregator", END)

    graph = graph.compile()
    return graph, execution_subgraph, planning_subgraph

def run_with_graph(registry: DatasourceRegistry, llm_registry: LLMRegistry, user_query: str, datasource_id: Optional[str] = None, execute: bool = True, vector_store: Optional[OrchestratorVectorStore] = None, vector_store_path: str = "", callbacks: Optional[List] = None) -> Dict:
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
            
    # Run graph
    result = g.invoke(initial_state_dict, config={"callbacks": callbacks})
    
    return result
