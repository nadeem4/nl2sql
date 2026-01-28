from typing import Callable, Dict, Optional, Union, List, Any
import json

from langchain_core.runnables import Runnable
from langgraph.graph import END, StateGraph
from langgraph.types import Send

from nl2sql.datasources import DatasourceRegistry
from nl2sql.pipeline.nodes.decomposer import DecomposerNode
from nl2sql.pipeline.nodes.datasource_resolver import DatasourceResolverNode
from nl2sql.pipeline.nodes.global_planner import GlobalPlannerNode
from nl2sql.pipeline.nodes.aggregator import EngineAggregatorNode
from nl2sql.pipeline.nodes.answer_synthesizer import AnswerSynthesizerNode

from nl2sql.pipeline.state import GraphState, SubgraphExecutionState
from nl2sql.auth import UserContext
from nl2sql.indexing.vector_store import VectorStore
from nl2sql.llm import LLMRegistry
from nl2sql.common.errors import PipelineError, ErrorSeverity, ErrorCode
from nl2sql.common.logger import trace_context, tenant_context
from nl2sql.context import NL2SQLContext
from nl2sql.pipeline.subgraphs import build_subgraph_registry, SubgraphOutput
from nl2sql.pipeline.nodes.datasource_resolver.schemas import DatasourceResolverResponse, ResolvedDatasource



def _next_scan_layer_ids(
    dag: "ExecutionDAG",
    artifact_refs: Dict[str, Any],
) -> List[str]:
    node_index = {n.node_id: n for n in dag.nodes}
    for layer in dag.layers or []:
        pending_scan = [
            node_id
            for node_id in layer
            if node_id in node_index
            and node_index[node_id].kind == "scan"
            and node_id not in artifact_refs
        ]
        if pending_scan:
            return pending_scan
    return []

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

    resolver_node = DatasourceResolverNode(ctx)
    decomposer_node = DecomposerNode(ctx)
    aggregator_node = EngineAggregatorNode(ctx)
    synthesizer_node = AnswerSynthesizerNode(ctx)
    global_planner_node = GlobalPlannerNode(ctx)

    subgraph_specs = build_subgraph_registry(ctx)
    subgraph_runnables = {
        name: spec.builder(ctx) for name, spec in subgraph_specs.items()
    }
    
    def _wrap_subgraph(subgraph, subgraph_name: str):
        def _get_state_value(state_obj, key: str, default=None):
            if isinstance(state_obj, dict):
                return state_obj.get(key, default)
            return getattr(state_obj, key, default)

        def _wrapper(state: GraphState):
            trace_id = _get_state_value(state, "trace_id")
            subgraph_id = _get_state_value(state, "subgraph_id") or f"{subgraph_name}:unknown:{trace_id}"
            if not trace_id and subgraph_id and ":" in subgraph_id:
                parts = subgraph_id.split(":")
                if len(parts) >= 3:
                    trace_id = parts[2]
            trace_id = trace_id or "unknown"
            sub_query_id = subgraph_id.split(":")[1] if ":" in subgraph_id else None
            selected_datasource_id = None
            sub_query = None
            decomposer_response = _get_state_value(state, "decomposer_response")
            for sq in (decomposer_response.sub_queries if decomposer_response else []):
                if sq.id == sub_query_id:
                    sub_query = sq
                    selected_datasource_id = sq.datasource_id
                    break

            sub_state = SubgraphExecutionState(
                trace_id=trace_id,
                user_context=_get_state_value(state, "user_context"),
                sub_query=sub_query,
                subgraph_id=subgraph_id,
                subgraph_name=subgraph_name,
            )
            result = subgraph.invoke(sub_state.model_dump())

            def _get_response_value(response: Any, attr: str):
                if response is None:
                    return None
                if hasattr(response, attr):
                    return getattr(response, attr)
                if isinstance(response, dict):
                    return response.get(attr)
                return None

            executor_response = result.get("executor_response")
            generator_response = result.get("generator_response")
            planner_response = result.get("ast_planner_response")

            sq_id = result.get("sub_query_id") or (sub_query.id if sub_query else None) or sub_query_id or "unknown"
            sub_reasoning = result.get("reasoning", [])
            datasource_id = result.get("selected_datasource_id") or selected_datasource_id
            if sub_query:
                datasource_id = sub_query.datasource_id

            schema_version = None
            resolver_response = _get_state_value(state, "datasource_resolver_response")
            if resolver_response:
                for entry in resolver_response.resolved_datasources:
                    entry_id = getattr(entry, "datasource_id", None)
                    if not entry_id and isinstance(entry, dict):
                        entry_id = entry.get("datasource_id")
                    if entry_id == datasource_id:
                        if hasattr(entry, "schema_version"):
                            schema_version = entry.schema_version
                        elif isinstance(entry, dict):
                            schema_version = entry.get("schema_version")
                        break

            artifact_refs = {}
            artifact = _get_response_value(executor_response, "artifact")
            if artifact:
                ctx.execution_store.put(sq_id, artifact)
                artifact_refs[sq_id] = artifact

            retry_count = result.get("retry_count", 0)
            status = "error" if result.get("errors") else "success"
            subgraph_output = SubgraphOutput(
                subgraph_id=subgraph_id,
                subgraph_name=subgraph_name,
                sub_query_id=sq_id,
                selected_datasource_id=datasource_id,
                schema_version=schema_version,
                retry_count=retry_count,
                plan=_get_response_value(planner_response, "plan"),
                sql_draft=_get_response_value(generator_response, "sql_draft"),
                artifact=artifact,
                errors=result.get("errors", []),
                reasoning=sub_reasoning,
                status=status,
            )

            return {
                "artifact_refs": artifact_refs,
                "subgraph_outputs": {subgraph_id: subgraph_output},
                "errors": result.get("errors", []),
                "reasoning": sub_reasoning
            }
        return _wrapper

    graph.add_node("datasource_resolver", resolver_node)
    graph.add_node("decomposer", decomposer_node)
    graph.add_node("global_planner", global_planner_node)
    for name, subgraph in subgraph_runnables.items():
        graph.add_node(name, _wrap_subgraph(subgraph, name))
    graph.add_node("aggregator", aggregator_node)
    graph.add_node("answer_synthesizer", synthesizer_node)
    graph.add_node("layer_router", lambda state: {})

    graph.set_entry_point("datasource_resolver")

    def _resolver_route(state: GraphState):
        resolver_response = state.datasource_resolver_response
        if not resolver_response:
            return "end"
        if not resolver_response.resolved_datasources or not resolver_response.allowed_datasource_ids:
            return "end"
        return "continue"

    graph.add_conditional_edges(
        "datasource_resolver",
        _resolver_route,
        {"continue": "decomposer", "end": END},
    )

    graph.add_edge("decomposer", "global_planner")

    def _resolve_subgraph(datasource_id: str) -> Optional[str]:
        try:
            caps = ctx.ds_registry.get_capabilities(datasource_id)
        except Exception:
            return None

        for name, spec in subgraph_specs.items():
            if spec.required_capabilities.issubset(caps):
                return name
        return None

    def _scan_payload(state: GraphState, subgraph_name: str, node_id: str) -> Dict[str, Any]:
        return {
            "subgraph_id": f"{subgraph_name}:{node_id}:{state.trace_id}",
            "subgraph_name": subgraph_name,
        }

    def route_scan_layers(state: GraphState):
        dag = getattr(state.global_planner_response, "execution_dag", None)
        decomposer_response = state.decomposer_response
        sub_queries = decomposer_response.sub_queries if decomposer_response else []
        sub_query_map = {sq.id: sq for sq in sub_queries}
        artifact_refs = state.artifact_refs or {}

        if not dag or not dag.layers:
            pending_ids = [sq.id for sq in sub_queries if sq.id not in artifact_refs]
            if not pending_ids:
                return [Send("aggregator", {})]
            target_ids = pending_ids
        else:
            node_index = {n.node_id: n for n in dag.nodes}
            target_ids = _next_scan_layer_ids(dag, artifact_refs)
            if not target_ids:
                return [Send("aggregator", {})]

        branches = []
        for node_id in target_ids:
            if node_id in sub_query_map:
                sq = sub_query_map[node_id]
                datasource_id = sq.datasource_id
            else:
                node = node_index.get(node_id) if dag else None
                if not node:
                    continue
                datasource_id = (node.attributes or {}).get("datasource_id")

            target = _resolve_subgraph(datasource_id)
            if not target:
                raise PipelineError(
                    node="layer_router",
                    message=f"No compatible subgraph found for datasource '{datasource_id}'.",
                    severity=ErrorSeverity.ERROR,
                    error_code=ErrorCode.INVALID_STATE,
                )
            payload = _scan_payload(state, target, node_id)
            branches.append(Send(target, payload))

        return branches

    graph.add_edge("global_planner", "layer_router")
    graph.add_conditional_edges(
        "layer_router",
        route_scan_layers,
        list(subgraph_runnables.keys()) + ["aggregator"],
    )

    for name in subgraph_runnables.keys():
        graph.add_edge(name, "layer_router")
    graph.add_edge("aggregator", "answer_synthesizer")
    graph.add_edge("answer_synthesizer", END)

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

    datasource_resolver_response = DatasourceResolverResponse()
    if datasource_id:
        datasource_resolver_response = DatasourceResolverResponse(
            resolved_datasources=[ResolvedDatasource(datasource_id=datasource_id, metadata={})],
            allowed_datasource_ids=[datasource_id],
            unsupported_datasource_ids=[],
        )
    initial_state = GraphState(
        user_query=user_query,
        user_context=user_context,
        datasource_resolver_response=datasource_resolver_response,
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
