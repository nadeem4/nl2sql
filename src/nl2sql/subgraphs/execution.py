from typing import Dict, Any, Optional
from langgraph.graph import StateGraph, END

from nl2sql.schemas import GraphState

from nl2sql.nodes.direct_sql.node import DirectSQLNode
from nl2sql.nodes.schema import SchemaNode
from nl2sql.nodes.executor import ExecutorNode
from nl2sql.subgraphs.agentic_execution_loop import build_agentic_execution_loop
from nl2sql.datasource_registry import DatasourceRegistry
from nl2sql.llm_registry import LLMRegistry
from nl2sql.vector_store import OrchestratorVectorStore

def format_result(state: GraphState) -> Dict[str, Any]:
    """Formats the execution result for the aggregator.

    Args:
        state: The current graph state.

    Returns:
        A dictionary containing the formatted intermediate results.
    """
    query = state.user_query
    execution = state.execution
    error = state.errors

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
        "execution": state.execution
    }

def build_execution_subgraph(registry: DatasourceRegistry, llm_registry: LLMRegistry, vector_store: Optional[OrchestratorVectorStore] = None, vector_store_path: str = ""):
    """Builds the execution subgraph.

    Routes between Fast Lane (Direct SQL) and Agentic Loop (Reasoning) based on
    the query response type.

    Args:
        registry: The datasource registry.
        llm_registry: The LLM registry.
        vector_store: Optional vector store.
        vector_store_path: Path to vector store.

    Returns:
        A tuple containing the compiled graph and the agentic execution loop.
    """
    graph = StateGraph(GraphState)

    schema_node = SchemaNode(registry=registry, vector_store=vector_store)
    direct_sql = DirectSQLNode(llm_registry.llm_map(), registry=registry)

    executor = ExecutorNode(registry=registry)

    effective_llm_map = {
        "planner": llm_registry.planner_llm(),
        "summarizer": llm_registry.summarizer_llm()
    }
    agentic_execution_loop = build_agentic_execution_loop(effective_llm_map, registry=registry, row_limit=1000)

    graph.add_node("schema", schema_node)
    graph.add_node("direct_sql", direct_sql)
    graph.add_node("fast_executor", executor)
    graph.add_node("agentic_execution_loop", agentic_execution_loop)
    graph.add_node("formatter", format_result)

    def route_based_on_complexity(state: GraphState) -> str:
        if state.response_type in ["tabular", "kpi"]:
            return "direct_sql"
        return "agentic_execution_loop"

    graph.set_entry_point("schema")

    graph.add_conditional_edges(
        "schema",
        route_based_on_complexity,
        {
            "direct_sql": "direct_sql",
            "agentic_execution_loop": "agentic_execution_loop"
        }
    )

    graph.add_edge("direct_sql", "fast_executor")
    graph.add_edge("fast_executor", "formatter")

    graph.add_edge("agentic_execution_loop", "formatter")

    graph.add_edge("formatter", END)

    return graph.compile(), agentic_execution_loop
