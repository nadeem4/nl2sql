from typing import Dict, Callable, Optional, Union
import dataclasses
from langchain_core.runnables import Runnable
from langgraph.graph import END, StateGraph

from nl2sql.schemas import GraphState
from nl2sql.nodes.planner.node import PlannerNode
from nl2sql.nodes.validator_node import ValidatorNode
from nl2sql.nodes.summarizer.node import SummarizerNode
from nl2sql.graph_utils import wrap_graphstate

LLMCallable = Union[Callable[[str], str], Runnable]

def build_planning_subgraph(llm_map: Dict[str, LLMCallable], row_limit: int = 100):
    """
    Builds the planning subgraph: Planner -> Validator -> Summarizer loop.

    Args:
        llm_map: Map of node names to LLM callables.
        row_limit: Row limit for validation context.

    Returns:
        Compiled StateGraph for the planning loop.
    """
    graph = StateGraph(GraphState)

    # Nodes
    planner = PlannerNode(llm=llm_map.get("planner"))
    validator = ValidatorNode(row_limit=row_limit)
    summarizer = SummarizerNode(llm=llm_map.get("summarizer"))

    def retry_node(state: GraphState) -> Dict:
        """Increments retry count."""
        return {"retry_count": state.retry_count + 1}

    def planner_retry_node(state: GraphState) -> Dict:
        """Increments retry count for planner retry path."""
        return {"retry_count": state.retry_count + 1}

    def check_planner(state: GraphState) -> str:
        """Checks if planner succeeded or needs retry/failure."""
        if not state.plan or any("Planner" in e for e in state.errors):
            if state.retry_count < 3:
                return "retry"
            else:
                return "end"
        return "ok"

    def check_validation(state: GraphState) -> str:
        """Checks validation results."""
        if state.errors:
            if any("Security Violation" in e for e in state.errors):
                return "end"
                
            if state.retry_count < 3:
                return "retry"
            else:
                return "end"
        return "ok"

    graph.add_node("planner", wrap_graphstate(planner, "planner"))
    graph.add_node("validator", wrap_graphstate(validator, "validator"))
    graph.add_node("summarizer", wrap_graphstate(summarizer, "summarizer"))
    graph.add_node("planner_retry", planner_retry_node)
    graph.add_node("retry_handler", retry_node)

    graph.set_entry_point("planner")

    graph.add_conditional_edges(
        "planner",
        check_planner,
        {
            "ok": "validator",
            "retry": "summarizer",
            "end": END # Failure
        }
    )

    graph.add_conditional_edges(
        "validator",
        check_validation,
        {
            "ok": END, # Success
            "retry": "retry_handler",
            "end": END # Failure
        }
    )

    # Retry Handler -> Summarizer
    graph.add_edge("retry_handler", "summarizer")

    # Summarizer -> Planner Retry -> Planner
    graph.add_edge("summarizer", "planner_retry")
    graph.add_edge("planner_retry", "planner")

    return graph.compile()
