from typing import Callable, Dict, Optional, Union, List
import json

from langchain_core.runnables import Runnable
from langgraph.graph import END, StateGraph
from langgraph.types import Send

from nl2sql.core.datasource_registry import DatasourceRegistry
from nl2sql.core.nodes.decomposer import DecomposerNode
from nl2sql.core.nodes.intent import IntentNode
from nl2sql.core.nodes.aggregator import AggregatorNode
from nl2sql.core.subgraphs.execution import build_execution_subgraph
from nl2sql.core.schemas import GraphState
from nl2sql.core.vector_store import OrchestratorVectorStore
from nl2sql.core.llm_registry import LLMRegistry
from nl2sql.core.errors import PipelineError, ErrorSeverity, ErrorCode


LLMCallable = Union[Callable[[str], str], Runnable]


def build_graph(
    registry: DatasourceRegistry,
    llm_registry: LLMRegistry,
    execute: bool = True,
    vector_store: Optional[OrchestratorVectorStore] = None,
    vector_store_path: str = "",
):
    graph = StateGraph(GraphState)

    intent_node = IntentNode(llm_registry.intent_classifier_llm())
    decomposer_node = DecomposerNode(
        llm_registry.decomposer_llm(), registry, vector_store
    )
    aggregator_node = AggregatorNode(llm_registry.aggregator_llm())

    execution_subgraph, agentic_execution_loop = build_execution_subgraph(
        registry, llm_registry, vector_store, vector_store_path
    )

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
            "row_count": row_count,
            "entity_ids": result.get("entity_ids", []),
            "reasoning": result.get("reasoning", {}),
        }

        return {
            "intermediate_results": result.get("intermediate_results", []),
            "query_history": [history_item],
            "errors": result.get("errors", []),
        }

    def report_missing_datasource(state: Dict):
        query = state.get("user_query", "Unknown Query")
        entity_ids = state.get("entity_ids", [])

        message = (
            f"Execution skipped for query '{query}'. "
            f"Missing datasource for entity_ids={entity_ids}"
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

    graph.add_node("intent", intent_node)
    graph.add_node("decomposer", decomposer_node)
    graph.add_node("execution_branch", execution_wrapper)
    graph.add_node("report_missing_datasource", report_missing_datasource)
    graph.add_node("aggregator", aggregator_node)

    graph.set_entry_point("intent")
    graph.add_edge("intent", "decomposer")

    def continue_to_subqueries(state: GraphState):
        branches = []

        if not state.sub_queries:
            return []

        entities_by_id = {}
        if state.entities:
            entities_by_id = {e.entity_id: e for e in state.entities}

        for sq in state.sub_queries:
            entity_ids = sq.entity_ids
            scoped_entities = [
                entities_by_id[eid]
                for eid in entity_ids
                if eid in entities_by_id
            ]

            payload = {
                "user_query": sq.query,
                "selected_datasource_id": sq.datasource_id,
                "entity_ids": entity_ids,
                "entities": scoped_entities,
                "complexity": sq.complexity,
                "reasoning": [
                    {
                        "node": f"execution_{sq.datasource_id}",
                        "content": f"Executing for entity_ids={entity_ids}",
                    }
                ],
            }

            if sq.datasource_id:
                branches.append(Send("execution_branch", payload))
            else:
                branches.append(Send("report_missing_datasource", payload))

        return branches

    graph.add_conditional_edges(
        "decomposer",
        continue_to_subqueries,
        ["execution_branch", "report_missing_datasource"],
    )

    graph.add_edge("execution_branch", "aggregator")
    graph.add_edge("report_missing_datasource", "aggregator")
    graph.add_edge("aggregator", END)

    return graph.compile(), execution_subgraph, agentic_execution_loop


def run_with_graph(
    registry: DatasourceRegistry,
    llm_registry: LLMRegistry,
    user_query: str,
    datasource_id: Optional[str] = None,
    execute: bool = True,
    vector_store: Optional[OrchestratorVectorStore] = None,
    vector_store_path: str = "",
    callbacks: Optional[List] = None,
) -> Dict:
    graph, _, _ = build_graph(
        registry,
        llm_registry,
        execute=execute,
        vector_store=vector_store,
        vector_store_path=vector_store_path,
    )

    initial_state = GraphState(
        user_query=user_query,
        selected_datasource_id=datasource_id,
        validation={"capabilities": "generic"},
    )

    return graph.invoke(
        initial_state.model_dump(),
        config={"callbacks": callbacks},
    )
