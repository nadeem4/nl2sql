from typing import Dict, Callable, Union
from langchain_core.runnables import Runnable
from langgraph.graph import END, StateGraph

from nl2sql.pipeline.state import GraphState
from nl2sql.pipeline.nodes.planner import PlannerNode
from nl2sql.pipeline.nodes.validator import ValidatorNode
from nl2sql.pipeline.nodes.summarizer.node import SummarizerNode
from nl2sql.pipeline.nodes.generator import GeneratorNode
from nl2sql.pipeline.nodes.executor import ExecutorNode
from nl2sql.datasources import DatasourceRegistry
from nl2sql.common.errors import ErrorCode

LLMCallable = Union[Callable[[str], str], Runnable]


def build_agentic_execution_loop(
    llm_map: Dict[str, LLMCallable],
    registry: DatasourceRegistry,
    row_limit: int = 100,
):
    graph = StateGraph(GraphState)

    planner = PlannerNode(registry=registry, llm=llm_map.get("planner"))
    validator = ValidatorNode(registry=registry, row_limit=row_limit)
    summarizer = SummarizerNode(llm=llm_map.get("summarizer"))
    generator = GeneratorNode(registry=registry)
    executor = ExecutorNode(registry=registry)

    def retry_node(state: GraphState) -> Dict:
        return {
            "retry_count": state.retry_count + 1,
            "entity_ids": getattr(state, "entity_ids", None),
            "entities": getattr(state, "entities", None),
        }

    def check_planner(state: GraphState) -> str:
        if not state.plan:
            if state.retry_count < 3:
                return "retry"
            return "end"
        return "ok"

    def check_validation(state: GraphState) -> str:
        if state.errors:
            if any(e.error_code == ErrorCode.SECURITY_VIOLATION for e in state.errors):
                return "end"
            if state.retry_count < 3:
                return "retry"
            return "end"
        return "ok"

    def check_execution(state: GraphState) -> str:
        if state.errors or (state.execution and state.execution.error):
            if state.retry_count < 3:
                return "retry"
            return "end"
        return "ok"

    graph.add_node("planner", planner)
    graph.add_node("validator", validator)
    graph.add_node("summarizer", summarizer)
    graph.add_node("sql_generator", generator)
    graph.add_node("executor", executor)
    graph.add_node("retry_handler", retry_node)

    graph.set_entry_point("planner")

    graph.add_conditional_edges(
        "planner",
        check_planner,
        {"ok": "validator", "retry": "retry_handler", "end": END},
    )

    graph.add_conditional_edges(
        "validator",
        check_validation,
        {"ok": "sql_generator", "retry": "retry_handler", "end": END},
    )

    graph.add_edge("sql_generator", "executor")

    graph.add_conditional_edges(
        "executor",
        check_execution,
        {"ok": END, "retry": "retry_handler", "end": END},
    )

    graph.add_edge("retry_handler", "summarizer")
    graph.add_edge("summarizer", "planner")

    return graph.compile()
