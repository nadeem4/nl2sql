from typing import Dict, Any, Optional
from langgraph.graph import StateGraph, END

from nl2sql.core.schemas import GraphState
from nl2sql.core.nodes.direct_sql.node import DirectSQLNode
from nl2sql.core.nodes.schema import SchemaNode
from nl2sql.core.nodes.executor import ExecutorNode
from nl2sql.core.subgraphs.agentic_execution_loop import build_agentic_execution_loop
from nl2sql.core.datasource_registry import DatasourceRegistry
from nl2sql.core.llm_registry import LLMRegistry
from nl2sql.core.vector_store import OrchestratorVectorStore


def format_result(state: GraphState) -> Dict[str, Any]:
    query = state.user_query
    execution = state.execution
    error = state.errors

    if state.response_type == "tabular":
        result_str = execution.rows
    else:
        if error:
            result_str = f"Query: {query}\nStatus: Error\nDetails: {error}"
        elif execution:
            rows = execution.rows
            row_count = execution.row_count
            if rows:
                result_data = f"{row_count} rows returned. Sample: {rows[:3]}"
            else:
                result_data = "No rows returned."
            result_str = f"Query: {query}\nStatus: Success\nData: {result_data}"
        else:
            result_str = f"Query: {query}\nStatus: No execution occurred."

    return {
        "intermediate_results": [result_str],
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

    schema_node = SchemaNode(registry=registry, vector_store=vector_store)
    direct_sql = DirectSQLNode(llm_registry.llm_map(), registry=registry)
    executor = ExecutorNode(registry=registry)

    effective_llm_map = {
        "planner": llm_registry.planner_llm(),
        "summarizer": llm_registry.summarizer_llm(),
    }

    agentic_execution_loop = build_agentic_execution_loop(
        effective_llm_map, registry=registry, row_limit=1000
    )

    graph.add_node("schema", schema_node)
    graph.add_node("direct_sql", direct_sql)
    graph.add_node("fast_executor", executor)
    graph.add_node("agentic_execution_loop", agentic_execution_loop)
    graph.add_node("formatter", format_result)

    def route_based_on_response_type(state: GraphState) -> str:
        if state.response_type in ["tabular", "kpi"]:
            return "direct_sql"
        return "agentic_execution_loop"

    def check_fast_lane_outcome(state: GraphState) -> str:
        if state.errors and len(state.errors) > 0:
            return "agentic_execution_loop"
        if not state.execution:
            return "agentic_execution_loop"
        return "formatter"

    graph.set_entry_point("schema")

    graph.add_conditional_edges(
        "schema",
        route_based_on_response_type,
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

    return graph.compile(), agentic_execution_loop
