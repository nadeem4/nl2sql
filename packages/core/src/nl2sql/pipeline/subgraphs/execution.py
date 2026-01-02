from typing import Dict, Any, Optional
from langgraph.graph import StateGraph, END

from nl2sql.pipeline.state import GraphState

from nl2sql.pipeline.subgraphs.sql_agent import build_sql_agent_graph
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
    return {
        "intermediate_results": [],
        "errors": error or []
    }


def build_execution_subgraph(
    registry: DatasourceRegistry,
    llm_registry: LLMRegistry,
    vector_store: Optional[OrchestratorVectorStore] = None,
    vector_store_path: str = "",
):
    graph = StateGraph(GraphState)
    
    effective_llm_map = {
        "planner": llm_registry.planner_llm(),
        "refiner": llm_registry.refiner_llm(),
    }

    sql_agent = build_sql_agent_graph(
        effective_llm_map, registry=registry, row_limit=1000
    )

    graph.add_node("sql_agent", sql_agent)
    graph.add_node("formatter", format_result)

    graph.set_entry_point("sql_agent")

    graph.add_edge("sql_agent", "formatter")
    graph.add_edge("formatter", END)

    return graph.compile()
