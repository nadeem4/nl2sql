from typing import Dict, Callable, Union
from langchain_core.runnables import Runnable
from langgraph.graph import END, StateGraph

from nl2sql.pipeline.state import GraphState
from nl2sql.pipeline.nodes.ast_planner import ASTPlannerNode
from nl2sql.pipeline.nodes.validator import LogicalValidatorNode, PhysicalValidatorNode
from nl2sql.pipeline.nodes.refiner import RefinerNode
from nl2sql.pipeline.nodes.generator import GeneratorNode
from nl2sql.pipeline.nodes.executor import ExecutorNode
from nl2sql.datasources import DatasourceRegistry
from nl2sql.common.errors import ErrorSeverity
from nl2sql.context import NL2SQLContext



import time
import random


def build_sql_agent_graph(
    ctx: NL2SQLContext,
):
    """Builds the SQL Agent Subgraph.

    Pipeline Flow:
    Planner -> LogicalValidator -> Generator -> PhysicalValidator -> Executor

    Feedback Loop:
    (LogicalValidator Error) -> RetryHandler -> Refiner -> Planner
    (PhysicalValidator Error) -> RetryHandler -> Refiner -> Planner
    """
    graph = StateGraph(GraphState)

    ast_planner = ASTPlannerNode(ctx)
    logical_validator = LogicalValidatorNode(ctx)
    physical_validator = PhysicalValidatorNode(ctx)
    refiner = RefinerNode(ctx)
    generator = GeneratorNode(ctx)
    executor = ExecutorNode(ctx)

    def retry_node(state: GraphState) -> Dict:
        """Increments retry count with exponential backoff and jitter."""
        count = state.retry_count
        base_delay = min(10.0, 1.0 * (2 ** count))
        jitter = random.uniform(0.0, 0.5)
        sleep_time = base_delay + jitter
        
        time.sleep(sleep_time)
        
        return {
            "retry_count": count + 1,
        }

    def check_planner(state: GraphState) -> str:
        """Routes based on planner result."""
        if not state.plan:
            # If explicit errors exist, check retryability
            if state.errors:
                 if not all(e.is_retryable for e in state.errors):
                     return "end"
            
            if state.retry_count < 3:
                return "retry"
            return "end"
        return "ok"

    def check_logical_validation(state: GraphState) -> str:
        """Routes based on logical validation result."""
        if state.errors:
            # Critical/Fatal errors stop execution immediately
            if not all(e.is_retryable for e in state.errors):
                return "end"
            
            if state.retry_count < 3:
                return "retry"
            return "end"
        return "ok"

    def check_physical_validation(state: GraphState) -> str:
        """Routes based on physical validation result."""
        if state.errors:
             # Critical/Fatal errors stop execution immediately
            if not all(e.is_retryable for e in state.errors):
                return "end"
            
            if state.retry_count < 3:
                return "retry"
            return "end"
        return "ok"

    graph.add_node("ast_planner", ast_planner)
    graph.add_node("logical_validator", logical_validator)
    graph.add_node("generator", generator)
    graph.add_node("physical_validator", physical_validator)
    graph.add_node("executor", executor)
    graph.add_node("refiner", refiner)
    graph.add_node("retry_handler", retry_node)

    graph.set_entry_point("ast_planner")

    graph.add_conditional_edges(
        "ast_planner",
        check_planner,
        {"ok": "logical_validator", "retry": "retry_handler", "end": END},
    )

    graph.add_conditional_edges(
        "logical_validator",
        check_logical_validation,
        {"ok": "generator", "retry": "retry_handler", "end": END},
    )

    graph.add_edge("generator", "physical_validator")

    graph.add_conditional_edges(
        "physical_validator",
        check_physical_validation,
        {"ok": "executor", "retry": "retry_handler", "end": END},
    )

    graph.add_edge("executor", END)

    graph.add_edge("retry_handler", "refiner")
    graph.add_edge("refiner", "ast_planner")

    return graph.compile()
