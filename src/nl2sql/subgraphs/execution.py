from typing import Dict, Any, Optional
from langgraph.graph import StateGraph, END

from nl2sql.schemas import GraphState
from nl2sql.nodes.router import RouterNode
from nl2sql.nodes.intent.node import IntentNode
from nl2sql.nodes.schema import SchemaNode
from nl2sql.nodes.generator_node import GeneratorNode
from nl2sql.nodes.executor_node import ExecutorNode
from nl2sql.subgraphs.planning import build_planning_subgraph
from nl2sql.graph_utils import wrap_graphstate
from nl2sql.datasource_registry import DatasourceRegistry
from nl2sql.llm_registry import LLMRegistry
from nl2sql.vector_store import SchemaVectorStore

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
        
    return {"intermediate_results": [result_str]}

def build_execution_subgraph(registry: DatasourceRegistry, llm_registry: LLMRegistry, vector_store: Optional[SchemaVectorStore] = None, vector_store_path: str = ""):
    """
    Builds the execution subgraph (Router -> Intent -> Schema -> Planning -> Generator -> Executor).
    """
    graph = StateGraph(GraphState)

    intent_llm = llm_registry.intent_llm()

    router = RouterNode(llm_registry, registry, vector_store_path)
    intent = IntentNode(llm=intent_llm)
    schema_node = SchemaNode(registry=registry, vector_store=vector_store)
    generator = GeneratorNode(registry=registry)
    executor = ExecutorNode(registry=registry)
    
    effective_llm_map = {
        "planner": llm_registry.planner_llm(),
        "summarizer": llm_registry.summarizer_llm()
    }
    planning_subgraph = build_planning_subgraph(effective_llm_map, row_limit=1000)

    graph.add_node("router", wrap_graphstate(router, "router"))
    graph.add_node("intent", wrap_graphstate(intent, "intent"))
    graph.add_node("schema", wrap_graphstate(schema_node, "schema"))
    graph.add_node("planning", planning_subgraph)
    graph.add_node("sql_generator", wrap_graphstate(generator, "sql_generator"))
    graph.add_node("executor", wrap_graphstate(executor, "executor"))
    graph.add_node("formatter", format_result)

    graph.set_entry_point("router")
    graph.add_edge("router", "intent")
    graph.add_edge("intent", "schema")
    graph.add_edge("schema", "planning")
    
    def check_planning_result(state: GraphState) -> str:
        if state.plan and not state.errors:
            return "ok"
        return "end"

    graph.add_conditional_edges(
        "planning",
        check_planning_result,
        {
            "ok": "sql_generator",
            "end": "formatter" 
        }
    )
    
    graph.add_edge("sql_generator", "executor")
    graph.add_edge("executor", "formatter")
    graph.add_edge("formatter", END)

    return graph.compile()
