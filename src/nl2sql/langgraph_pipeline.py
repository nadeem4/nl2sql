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
from nl2sql.schemas import GraphState, DecomposerResponse
from nl2sql.nodes.aggregator.schemas import AggregatedResponse
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

    llm_map = {
        "canonicalizer": llm_registry.canonicalizer_llm(),
        "decomposer": llm_registry.decomposer_llm(),
    }

    decomposer = DecomposerNode(llm_map, registry, vector_store)
    
    aggregator_llm = llm_registry.aggregator_llm()
    aggregator = AggregatorNode(aggregator_llm)
    

    execution_subgraph, agentic_execution_loop = build_execution_subgraph(registry, llm_registry, vector_store, vector_store_path)

    def execution_wrapper(state: Union[Dict, GraphState]):
        import time
        start = time.perf_counter()
        
        result = execution_subgraph.invoke(state)
        duration = time.perf_counter() - start
        
        sql = result.get("sql_draft")
        
        execution = result.get("execution")
        row_count = 0
        if execution:
             row_count = execution.get("row_count") if isinstance(execution, dict) else getattr(execution, "row_count", 0)
        selected_id = result.get("selected_datasource_id")

        ds_type = "Unknown"
        if selected_id:
            try:
                profile = registry.get_profile(selected_id)
                ds_type = profile.engine
            except Exception:
                pass

        history_item = {
            "datasource_id": selected_id,
            "datasource_type": ds_type,
            "sub_query": result.get("user_query"),
            "sql": sql,
            "row_count": row_count,
            "reasoning": result.get("reasoning", {})
        }
        
        return {
            "intermediate_results": result.get("intermediate_results", []),
            "query_history": [history_item],
            "errors": result.get("errors", [])
        }

    def report_missing_datasource(state: Union[Dict, GraphState]):
        """
        Node to report an error when a subquery lacks a datasource ID.
        """
        from nl2sql.errors import PipelineError, ErrorSeverity, ErrorCode
        
        query = state.get("user_query", "Unknown Query")
        
        reasoning = state.get("reasoning", "No reasoning provided.")
        
        logging_err = f"Execution Skipped: No datasource_id provided for query '{query}'. Reasoning: {reasoning}"
        
        return {
            "errors": [
                 PipelineError(
                    node="router", 
                    message=logging_err, 
                    severity=ErrorSeverity.WARNING,
                    error_code=ErrorCode.MISSING_DATASOURCE_ID
                )
            ],
            "intermediate_results": [f"Skipped execution: {logging_err}"]
        }

    # Add Nodes
    graph.add_node("decomposer", decomposer)
    graph.add_node("execution_branch", execution_wrapper)
    graph.add_node("report_missing_datasource", report_missing_datasource)
    graph.add_node("aggregator", aggregator)

    # Edges
    graph.set_entry_point("decomposer")
    
    def continue_to_subqueries(state: GraphState):
        """
        Determines whether to fan out to parallel branches or proceed with a single branch.
        Uses structured SubQuery objects to pre-route.
        """
        
        branches = []
        
        if state.sub_queries is None:
            return []

        for sq in state.sub_queries:  
            sq_text = sq.query 
            ds_id = sq.datasource_id
            candidate_tables = sq.candidate_tables 
            reasoning = sq.reasoning
            
            payload = {
                "user_query": sq_text, 
                "selected_datasource_id": ds_id,
                "candidate_tables": candidate_tables,
                "reasoning": [{"node": f"agentic_execution_loop_{ds_id} ({sq_text})", "content": reasoning}] 
            }
            
            if ds_id:
                branches.append(Send("execution_branch", payload))
            else:
                branches.append(Send("report_missing_datasource", payload))
            
        return branches

    graph.add_conditional_edges(
        "decomposer",
        continue_to_subqueries,
        ["execution_branch", "report_missing_datasource"]
    )
    
    graph.add_edge("execution_branch", "aggregator")
    graph.add_edge("report_missing_datasource", "aggregator")
    graph.add_edge("aggregator", END)

    graph = graph.compile()
    return graph, execution_subgraph, agentic_execution_loop

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
    g, execution_subgraph, agentic_execution_loop = build_graph(registry, llm_registry, execute=execute, vector_store=vector_store, vector_store_path=vector_store_path)
    
    initial_state = GraphState(
        user_query=user_query,
        selected_datasource_id=datasource_id,
        validation={"capabilities": "generic"}, 
    )

    initial_state_dict = initial_state.model_dump()
            
    # Run graph
    result = g.invoke(initial_state_dict, config={"callbacks": callbacks})
    
    return result
