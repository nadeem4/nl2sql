from typing import Dict, Callable, Optional, Union
import dataclasses
from langchain_core.runnables import Runnable
from langgraph.graph import END, StateGraph

from nl2sql.schemas import GraphState
from nl2sql.nodes.intent import IntentNode
from nl2sql.nodes.schema import SchemaNode
from nl2sql.nodes.planner import PlannerNode
from nl2sql.nodes.validator import ValidatorNode
from nl2sql.nodes.summarizer.node import SummarizerNode
from nl2sql.nodes.generator import GeneratorNode
from nl2sql.nodes.executor import ExecutorNode
from nl2sql.datasource_registry import DatasourceRegistry

LLMCallable = Union[Callable[[str], str], Runnable]

def build_planning_subgraph(llm_map: Dict[str, LLMCallable], registry: DatasourceRegistry, row_limit: int = 100):
    """
    Builds the planning subgraph: Planner -> Validator -> Generator -> Executor -> (Error) -> Summarizer -> Planner.

    Args:
        llm_map: Map of node names to LLM callables.
        registry: Datasource registry.
        row_limit: Row limit for validation context.

    Returns:
        Compiled StateGraph for the planning loop.
    """
    graph = StateGraph(GraphState)

    # Nodes
    planner = PlannerNode(registry=registry, llm=llm_map.get("planner"))
    validator = ValidatorNode(registry=registry, row_limit=row_limit)
    summarizer = SummarizerNode(llm=llm_map.get("summarizer"))
    generator = GeneratorNode(registry=registry)
    executor = ExecutorNode(registry=registry)

    def retry_node(state: GraphState) -> Dict:
        """Increments retry count."""
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

    def check_execution(state: GraphState) -> str:
        """Checks execution results."""
        if state.errors or (state.execution and state.execution.error):
            if state.retry_count < 3:
                return "retry"
            else:
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
        {
            "ok": "validator",
            "retry": "retry_handler", 
            "end": END 
        }
    )

    graph.add_conditional_edges(
        "validator",
        check_validation,
        {
            "ok": "sql_generator",
            "retry": "retry_handler",
            "end": END 
        }
    )
    
    graph.add_edge("sql_generator", "executor")
    
    graph.add_conditional_edges(
        "executor",
        check_execution,
        {
            "ok": END, 
            "retry": "retry_handler",
            "end": END 
        }
    )

    graph.add_edge("retry_handler", "summarizer")
    graph.add_edge("summarizer", "planner")

    return graph.compile()
