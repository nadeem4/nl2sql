from typing import Callable, Dict, Optional, Union, List, Any
import json

from langchain_core.runnables import Runnable
from langgraph.graph import END, StateGraph
from langgraph.types import Send

from nl2sql.datasources import DatasourceRegistry
from nl2sql.pipeline.nodes.decomposer import DecomposerNode
from nl2sql.pipeline.nodes.aggregator import AggregatorNode
from nl2sql.pipeline.nodes.semantic import SemanticAnalysisNode
from nl2sql.pipeline.nodes.intent_validator import IntentValidatorNode

from nl2sql.pipeline.state import GraphState, UserContext
from nl2sql.services.vector_store import OrchestratorVectorStore
from nl2sql.llm import LLMRegistry
from nl2sql.common.errors import PipelineError, ErrorSeverity, ErrorCode
from nl2sql.common.logger import trace_context, tenant_context
from nl2sql.context import NL2SQLContext
from nl2sql.pipeline.subgraphs.sql_agent import build_sql_agent_graph

def build_graph(
    ctx: NL2SQLContext,
    execute: bool = True,
) -> StateGraph:
    """Builds the main LangGraph pipeline.

    Constructs the graph with Semantic Analysis, Decomposer, Execution branches,
    and Aggregator.

    Args:
        ctx (NL2SQLContext): The application context containing registries and services.
        execute (bool): Whether to allow execution against real databases.

    Returns:
        StateGraph: The compiled LangGraph runnable.
    """
    graph = StateGraph(GraphState)

    decomposer_node = DecomposerNode(ctx)
    aggregator_node = AggregatorNode(ctx)

    sql_agent_subgraph = build_sql_agent_graph(ctx)
    semantic_node = SemanticAnalysisNode(ctx)
    intent_validator_node = IntentValidatorNode(ctx)

    def check_intent(state: GraphState):
        """Conditional check for intent violations."""
        errors = state.errors or []
        if any(e.error_code == ErrorCode.INTENT_VIOLATION for e in errors):
            return "end"
        return "continue"

    def sql_agent_wrapper(state: GraphState):
        result = sql_agent_subgraph.invoke(state)

        selected_id = result.get("selected_datasource_id")
        execution = result.get("execution")
        history_item = {
            "datasource_id": selected_id,
            "sub_query": result.get("user_query"),
            "sql": result.get("sql_draft"),
            "reasoning": result.get("reasoning", {}),
            "execution": execution,
        }

        return {
            "query_history": [history_item],
            "errors": result.get("errors", []),
        }

    def report_missing_datasource(state: GraphState):
        query = state.get("user_query", "Unknown Query")

        message = (
            f"Execution skipped for query '{query}'. "
            f"Missing datasource assignment."
        )

        return {
            "errors": [
                PipelineError(
                    node="router",
                    message=message,
                    severity=ErrorSeverity.WARNING,
                    error_code=ErrorCode.MISSING_DATASOURCE_ID,
                )
            ],
        }

    graph.add_node("semantic_analysis", semantic_node)
    graph.add_node("intent_validator", intent_validator_node)
    graph.add_node("decomposer", decomposer_node)
    graph.add_node("sql_agent_subgraph", sql_agent_wrapper)
    graph.add_node("report_missing_datasource", report_missing_datasource)
    graph.add_node("aggregator", aggregator_node)

    graph.set_entry_point("semantic_analysis")
    
    graph.add_edge("semantic_analysis", "intent_validator")
    
    graph.add_conditional_edges(
        "intent_validator",
        check_intent,
        {"continue": "decomposer", "end": "aggregator"} 
    )

    def continue_to_subqueries(state: GraphState):
        branches = []

        if not state.sub_queries:
            return []

        for sq in state.sub_queries:
            if sq.datasource_id is None:
                branches.append(Send("report_missing_datasource", {"user_query": sq.query}))
            
            payload = {
                "trace_id": state.trace_id,
                "user_query": sq.query,
                "selected_datasource_id": sq.datasource_id,
                "complexity": sq.complexity,
                "relevant_tables": sq.relevant_tables,
                "reasoning": [
                    {
                        "node": f"execution_{sq.id}",
                        "content": f"Executing sub-query ({sq.query}) for {sq.datasource_id}",
                    }
                ],
                "user_context": state.user_context
            }

            branches.append(Send("sql_agent", payload))

        return branches

    graph.add_conditional_edges(
        "decomposer",
        continue_to_subqueries,
        ["sql_agent", "report_missing_datasource"],
    )

    graph.add_edge("sql_agent", "aggregator")
    graph.add_edge("report_missing_datasource", "aggregator")
    graph.add_edge("aggregator", END)

    return graph.compile()


def run_with_graph(
    ctx: NL2SQLContext,
    user_query: str,
    datasource_id: Optional[str] = None,
    execute: bool = True,
    callbacks: Optional[List] = None,
    user_context: UserContext = None,
) -> Dict:
    """Convenience function to run the full pipeline.

    Args:
        ctx (NL2SQLContext): The application context.
        user_query (str): The user's question.
        datasource_id (Optional[str]): specific datasource to target (optional).
        execute (bool): Whether to execute against real databases.
        callbacks (Optional[List]): LangChain callbacks.
        user_context (UserContext): User identity/permissions.

    Returns:
        Dict: Final execution result.
    """
    graph = build_graph(
        ctx,
        execute=execute,
    )

    initial_state = GraphState(
        user_query=user_query,
        selected_datasource_id=datasource_id,
        user_context=user_context,
    )

    from nl2sql.common.settings import settings
    import concurrent.futures
    import traceback

    timeout_sec = settings.global_timeout_sec

    def _invoke():
        return graph.invoke(
            initial_state.model_dump(),
            config={"callbacks": callbacks},
        )

    try:
        # Use configured thread pool size for pipeline execution
        with concurrent.futures.ThreadPoolExecutor(max_workers=settings.sandbox_exec_workers) as executor:
            future = executor.submit(_invoke)
            return future.result(timeout=timeout_sec)
    except concurrent.futures.TimeoutError:
        error_msg = f"Pipeline execution timed out after {timeout_sec} seconds."
        return {
            "errors": [
                PipelineError(
                    node="orchestrator",
                    message=error_msg,
                    severity=ErrorSeverity.ERROR,
                    error_code=ErrorCode.PIPELINE_TIMEOUT,
                )
            ],
            "final_answer": "I apologize, but the request timed out. Please try again with a simpler query."
        }
    except Exception as e:
        # Fallback for other runtime crashes
        return {
            "errors": [
                 PipelineError(
                    node="orchestrator",
                    message=f"Pipeline crashed: {str(e)}",
                    severity=ErrorSeverity.ERROR,
                    error_code=ErrorCode.UNKNOWN_ERROR,
                    stack_trace=traceback.format_exc()
                )
            ]
        }
