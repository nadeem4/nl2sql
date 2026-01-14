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
from nl2sql.pipeline.subgraphs.execution import build_execution_subgraph
from nl2sql.pipeline.state import GraphState, UserContext
from nl2sql.services.vector_store import OrchestratorVectorStore
from nl2sql.services.llm import LLMRegistry
from nl2sql.common.errors import PipelineError, ErrorSeverity, ErrorCode
from nl2sql.common.logger import trace_context, tenant_context


LLMCallable = Union[Callable[[str], str], Runnable]


def traced_node(node: Callable):
    """Wraps a node to inject trace_id and tenant_id from state into the logging context."""
    def wrapper(state: Union[Dict, Any]):
        # Extract trace_id and tenant_id from state
        tid = None
        tenant_id = None
        
        if isinstance(state, dict):
            tid = state.get("trace_id")
            # user_context might be dict or object depending on serialization state in LangGraph
            uc = state.get("user_context")
            if isinstance(uc, dict):
                tenant_id = uc.get("tenant_id")
            elif hasattr(uc, "tenant_id"):
                tenant_id = uc.tenant_id
        else:
            tid = getattr(state, "trace_id", None)
            uc = getattr(state, "user_context", None)
            if uc:
               tenant_id = getattr(uc, "tenant_id", None)
            
        if tid:
            with trace_context(tid):
                with tenant_context(tenant_id):
                    return node(state)
        return node(state)
    return wrapper


def build_graph(
    registry: DatasourceRegistry,
    llm_registry: LLMRegistry,
    execute: bool = True,
    vector_store: Optional[OrchestratorVectorStore] = None,
    vector_store_path: str = "",
) -> StateGraph:
    """Builds the main LangGraph pipeline.

    Constructs the graph with Semantic Analysis, Decomposer, Execution branches,
    and Aggregator.

    Args:
        registry (DatasourceRegistry): Registry of datasources.
        llm_registry (LLMRegistry): Registry of LLM providers.
        execute (bool): Whether to allow execution against real databases.
        vector_store (Optional[OrchestratorVectorStore]): RAG vector store.
        vector_store_path (str): Path to local vector store if needed.

    Returns:
        StateGraph: The compiled LangGraph runnable.
    """
    graph = StateGraph(GraphState)

    decomposer_node = DecomposerNode(
        llm_registry.decomposer_llm(), 
        registry, 
        vector_store
    )
    aggregator_node = AggregatorNode(llm_registry.aggregator_llm())

    execution_subgraph = build_execution_subgraph(
        registry, llm_registry, vector_store, vector_store_path
    )
    semantic_node = SemanticAnalysisNode(llm_registry.semantic_llm())
    intent_validator_node = IntentValidatorNode(llm_registry.intent_validator_llm())

    def check_intent(state: GraphState):
        """Conditional check for intent violations."""
        errors = state.errors or []
        if any(e.error_code == ErrorCode.INTENT_VIOLATION for e in errors):
            return "end"
        return "continue"

    def execution_wrapper(state: Dict):
        result = execution_subgraph.invoke(state)

        selected_id = result.get("selected_datasource_id")
        execution = result.get("execution")
        row_count = 0

        if execution:
            row_count = (
                execution.get("row_count")
                if isinstance(execution, dict)
                else getattr(execution, "row_count", 0)
            )

        history_item = {
            "datasource_id": selected_id,
            "sub_query": result.get("user_query"),
            "sql": result.get("sql_draft"),
            "reasoning": result.get("reasoning", {}),
            "row_count": row_count,
        }

        return {
            "intermediate_results": result.get("intermediate_results", []),
            "query_history": [history_item],
            "errors": result.get("errors", []),
        }

    def report_missing_datasource(state: Dict):
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
            "intermediate_results": [message],
        }

    graph.add_node("semantic_analysis", traced_node(semantic_node))
    graph.add_node("intent_validator", traced_node(intent_validator_node))
    graph.add_node("decomposer", traced_node(decomposer_node))
    graph.add_node("execution_branch", traced_node(execution_wrapper))
    graph.add_node("report_missing_datasource", traced_node(report_missing_datasource))
    graph.add_node("aggregator", traced_node(aggregator_node))

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
                        "node": f"execution_{sq.datasource_id}_({sq.query})",
                        "content": f"Executing sub-query ({sq.query}) for {sq.datasource_id}",
                    }
                ],
                "user_context": state.user_context
            }

            branches.append(Send("execution_branch", payload))

        return branches

    graph.add_conditional_edges(
        "decomposer",
        continue_to_subqueries,
        ["execution_branch", "report_missing_datasource"],
    )

    graph.add_edge("execution_branch", "aggregator")
    graph.add_edge("report_missing_datasource", "aggregator")
    graph.add_edge("aggregator", END)

    return graph.compile()


def run_with_graph(
    registry: DatasourceRegistry,
    llm_registry: LLMRegistry,
    user_query: str,
    datasource_id: Optional[str] = None,
    execute: bool = True,
    vector_store: Optional[OrchestratorVectorStore] = None,
    vector_store_path: str = "",
    callbacks: Optional[List] = None,
    user_context: UserContext = None,
) -> Dict:
    """Convenience function to run the full pipeline.

    Args:
        registry (DatasourceRegistry): Registry of datasources.
        llm_registry (LLMRegistry): Registry of LLM providers.
        user_query (str): The user's question.
        datasource_id (Optional[str]): specific datasource to target (optional).
        execute (bool): Whether to execute against real databases.
        vector_store (Optional[OrchestratorVectorStore]): RAG vector store.
        vector_store_path (str): Path to local vector store.
        callbacks (Optional[List]): LangChain callbacks.
        user_context (UserContext): User identity/permissions.

    Returns:
        Dict: Final execution result.
    """
    graph = build_graph(
        registry,
        llm_registry,
        execute=execute,
        vector_store=vector_store,
        vector_store_path=vector_store_path,
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
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
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
