from typing import Dict, Callable, Union
from langchain_core.runnables import Runnable
from langgraph.graph import END, StateGraph

from nl2sql.pipeline.state import GraphState
from nl2sql.pipeline.nodes.planner import PlannerNode
from nl2sql.pipeline.nodes.validator import LogicalValidatorNode, PhysicalValidatorNode
from nl2sql.pipeline.nodes.refiner import RefinerNode
from nl2sql.pipeline.nodes.generator import GeneratorNode
from nl2sql.pipeline.nodes.executor import ExecutorNode
from nl2sql.datasources import DatasourceRegistry
from nl2sql.common.errors import ErrorSeverity

LLMCallable = Union[Callable[[str], str], Runnable]


def build_sql_agent_graph(
    llm_map: Dict[str, LLMCallable],
    registry: DatasourceRegistry,
    row_limit: int = 100,
):
    """Builds the SQL Agent Subgraph.

    Pipeline Flow:
    Planner -> LogicalValidator -> Generator -> PhysicalValidator -> Executor

    Feedback Loop:
    (LogicalValidator Error) -> RetryHandler -> Refiner -> Planner
    (PhysicalValidator Error) -> RetryHandler -> Refiner -> Planner
    """
    graph = StateGraph(GraphState)

    planner = PlannerNode(registry=registry, llm=llm_map.get("planner"))
    logical_validator = LogicalValidatorNode(registry=registry)
    physical_validator = PhysicalValidatorNode(registry=registry, row_limit=row_limit)
    refiner = RefinerNode(llm=llm_map.get("refiner"))
    generator = GeneratorNode(registry=registry)
    executor = ExecutorNode(registry=registry)

    def retry_node(state: GraphState) -> Dict:
        return {
            "retry_count": state.retry_count + 1,
        }

    def check_planner(state: GraphState) -> str:
        if not state.plan:
            if state.retry_count < 3:
                return "retry"
            return "end"
        return "ok"

    def check_logical_validation(state: GraphState) -> str:
        if state.errors:
            # Critical errors (e.g., Security) might stop execution immediately
            if any(e.severity == ErrorSeverity.CRITICAL for e in state.errors):
                return "end"
            if state.retry_count < 3:
                return "retry"
            return "end"
        return "ok"

    def check_physical_validation(state: GraphState) -> str:
        if state.errors:
            if any(e.severity == ErrorSeverity.CRITICAL for e in state.errors):
                return "end"
            if state.retry_count < 3:
                return "retry"
            return "end"
        return "ok"

    graph.add_node("planner", planner)
    graph.add_node("logical_validator", logical_validator)
    graph.add_node("generator", generator)
    graph.add_node("physical_validator", physical_validator)
    graph.add_node("executor", executor)
    graph.add_node("refiner", refiner)
    graph.add_node("retry_handler", retry_node)

    graph.set_entry_point("planner")

    # Step 1: Planner
    graph.add_conditional_edges(
        "planner",
        check_planner,
        {"ok": "logical_validator", "retry": "retry_handler", "end": END},
    )

    # Step 2: Logical Validation (AST)
    graph.add_conditional_edges(
        "logical_validator",
        check_logical_validation,
        {"ok": "generator", "retry": "retry_handler", "end": END},
    )

    # Step 3: SQL Generation
    graph.add_edge("generator", "physical_validator")

    # Step 4: Physical Validation (SQL Safety/Dry Run)
    graph.add_conditional_edges(
        "physical_validator",
        check_physical_validation,
        {"ok": "executor", "retry": "retry_handler", "end": END},
    )

    # Step 5: Execution
    graph.add_edge("executor", END)

    # Feedback Loop
    graph.add_edge("retry_handler", "refiner")
    graph.add_edge("refiner", "planner")

    return graph.compile()
