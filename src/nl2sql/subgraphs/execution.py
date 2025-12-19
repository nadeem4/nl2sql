from typing import Dict, Any, Optional
from langgraph.graph import StateGraph, END

from nl2sql.schemas import GraphState

from nl2sql.nodes.intent.node import IntentNode
from nl2sql.nodes.schema import SchemaNode
from nl2sql.nodes.generator import GeneratorNode
from nl2sql.nodes.executor import ExecutorNode
from nl2sql.subgraphs.agentic_execution_loop import build_agentic_execution_loop
from nl2sql.datasource_registry import DatasourceRegistry
from nl2sql.llm_registry import LLMRegistry
from nl2sql.vector_store import OrchestratorVectorStore

def format_result(state: GraphState) -> Dict[str, Any]:
    """
    Formats the execution result for the aggregator.
    """
    query = state.user_query
    execution = state.execution
    error = state.errors
    
    if error:
        result_str = f"Query: {query}\nStatus: Error\nDetails: {error}"
    elif execution:
        # Handle ExecutionModel
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
    """
    Builds the execution subgraph (Intent -> Schema -> Planning/Reasoning Loop).
    """
    graph = StateGraph(GraphState)

    intent_llm = llm_registry.intent_llm()

    intent = IntentNode(llm=intent_llm)
    schema_node = SchemaNode(registry=registry, vector_store=vector_store)
    
    effective_llm_map = {
        "planner": llm_registry.planner_llm(),
        "summarizer": llm_registry.summarizer_llm()
    }
    agentic_execution_loop = build_agentic_execution_loop(effective_llm_map, registry=registry, row_limit=1000)

    graph.add_node("intent", intent)
    graph.add_node("schema", schema_node)
    graph.add_node("agentic_execution_loop", agentic_execution_loop)
    graph.add_node("formatter", format_result)

    def check_entry_condition(state: GraphState) -> str:
        """
        Determines the entry point based on whether candidate tables are already known.
        """
        if state.candidate_tables:
            return "schema"
        return "intent"

    graph.set_conditional_entry_point(
        check_entry_condition,
        {
            "intent": "intent",
            "schema": "schema"
        }
    )
    graph.add_edge("intent", "schema")

    
    graph.add_edge("schema", "agentic_execution_loop")
    
    graph.add_edge("agentic_execution_loop", "formatter")
    graph.add_edge("formatter", END)

    return graph.compile(), agentic_execution_loop
