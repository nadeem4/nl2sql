from typing import Dict, Any, Optional
from langgraph.graph import StateGraph, END

from nl2sql.pipeline.state import GraphState
from nl2sql.pipeline.nodes.direct_sql.node import DirectSQLNode

from nl2sql.pipeline.nodes.executor import ExecutorNode
from nl2sql.pipeline.subgraphs.agentic_execution_loop import build_agentic_execution_loop
from nl2sql.datasources import DatasourceRegistry
from nl2sql.services.llm import LLMRegistry
from nl2sql.services.vector_store import OrchestratorVectorStore


def format_result(state: GraphState) -> Dict[str, Any]:
    query = state.user_query
    execution = state.execution
    error = state.errors

    # Default formatting logic
    if error:
        result_str = f"Query: {query}\nStatus: Error\nDetails: {error}"
    elif execution:
        rows = execution.rows
        return {
            "intermediate_results": [rows], 
            "selected_datasource_id": state.selected_datasource_id,
            "sql_draft": state.sql_draft,
            "execution": execution,
            "entity_ids": getattr(state, "entity_ids", []),
        }


def build_execution_subgraph(
    registry: DatasourceRegistry,
    llm_registry: LLMRegistry,
    vector_store: Optional[OrchestratorVectorStore] = None,
    vector_store_path: str = "",
):
    graph = StateGraph(GraphState)
    
    direct_sql = DirectSQLNode(llm_registry.llm_map(), registry=registry)
    executor = ExecutorNode(registry=registry)

    effective_llm_map = {
        "planner": llm_registry.planner_llm(),
        "summarizer": llm_registry.summarizer_llm(),
    }

    agentic_execution_loop = build_agentic_execution_loop(
        effective_llm_map, registry=registry, row_limit=1000
    )

    graph.add_node("direct_sql", direct_sql)
    graph.add_node("fast_executor", executor)
    graph.add_node("agentic_execution_loop", agentic_execution_loop)
    graph.add_node("formatter", format_result)

    def route_based_on_complexity(state: GraphState) -> str:
        if state.complexity == "simple":
            return "direct_sql"
        return "agentic_execution_loop"

    def check_fast_lane_outcome(state: GraphState) -> str:
        if state.errors and len(state.errors) > 0:
            return "agentic_execution_loop"
        if not state.execution:
            return "agentic_execution_loop"
        return "formatter"

    graph.set_conditional_entry_point(
        route_based_on_complexity,
        {
            "direct_sql": "direct_sql",
            "agentic_execution_loop": "agentic_execution_loop",
        },
    )

    graph.add_edge("direct_sql", "fast_executor")

    graph.add_conditional_edges(
        "fast_executor",
        check_fast_lane_outcome,
        {
            "formatter": "formatter",
            "agentic_execution_loop": "agentic_execution_loop",
        },
    )

    graph.add_edge("agentic_execution_loop", "formatter")
    graph.add_edge("formatter", END)

    return graph.compile()
