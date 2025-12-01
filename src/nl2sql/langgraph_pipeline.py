from typing import Callable, Dict, Optional, Union
import dataclasses
import json
from sqlalchemy import inspect
from langchain_core.runnables import Runnable

from nl2sql.capabilities import get_capabilities
from nl2sql.datasource_config import DatasourceProfile
from nl2sql.engine_factory import make_engine, run_read_query
from nl2sql.nodes.intent.node import IntentNode
from nl2sql.nodes.planner.node import PlannerNode
from nl2sql.nodes.generator_node import GeneratorNode
from nl2sql.nodes.validator_node import ValidatorNode
from nl2sql.nodes.schema_node import SchemaNode
from nl2sql.schemas import GraphState
from nl2sql.tracing import span
from nl2sql.vector_store import SchemaVectorStore

# Type for an LLM callable: prompt -> string
LLMCallable = Union[Callable[[str], str], Runnable]



def _wrap_graphstate(fn: Callable[[GraphState], GraphState], name: Optional[str] = None):
    """
    LangGraph expects and returns dict-like state; wrap dataclass functions.
    """

    def wrapped(state: Dict) -> Dict:
        gs = GraphState(**state)
        
        # Determine node name
        node_name = name
        if not node_name:
            node_name = getattr(fn, "__name__", None)
        if not node_name and hasattr(fn, "func"):
            node_name = getattr(fn.func, "__name__", None)
        if not node_name:
            node_name = type(fn).__name__ if hasattr(fn, "__class__") else "node"
            
        import time
        from nl2sql.logger import get_logger
        
        logger = get_logger(node_name)
        
        start = time.perf_counter()
        try:
            with span(node_name):
                gs = fn(gs)
            duration = time.perf_counter() - start
            
            gs.latency[node_name] = duration
            
            # Log success
            logger.info(f"Node {node_name} completed", extra={
                "node": node_name,
                "duration_ms": duration * 1000,
                "status": "success"
            })
            
        except Exception as e:
            duration = time.perf_counter() - start
            logger.error(f"Node {node_name} failed: {e}", extra={
                "node": node_name,
                "duration_ms": duration * 1000,
                "status": "error",
                "error": str(e)
            })
            raise e
        
        return dataclasses.asdict(gs)

    return wrapped





from nl2sql.nodes.executor_node import ExecutorNode


def build_graph(profile: DatasourceProfile, llm: Optional[LLMCallable] = None, llm_map: Optional[Dict[str, LLMCallable]] = None, execute: bool = True, vector_store: Optional[SchemaVectorStore] = None):
    try:
        from langgraph.graph import END, StateGraph
    except ImportError as exc:
        raise RuntimeError("langgraph is required to build the graph. Install via pip.") from exc

    # Attach capabilities marker for downstream routing
    base_state = dataclasses.asdict(GraphState(user_query=""))
    base_state["validation"]["capabilities"] = get_capabilities(profile.engine).dialect

    graph = StateGraph(dict)

    node_llm = lambda name: (llm_map or {}).get(name, llm) if llm_map else llm

    intent = IntentNode(llm=node_llm("intent"))
    schema_node = SchemaNode(profile=profile, vector_store=vector_store)
    planner = PlannerNode(llm=node_llm("planner"))
    generator = GeneratorNode(profile_engine=profile.engine, row_limit=profile.row_limit)
    validator = ValidatorNode(row_limit=profile.row_limit)

    def retry_node(state: Dict) -> Dict:
        gs = GraphState(**state)
        gs.retry_count += 1
        return dataclasses.asdict(gs)

    def planner_retry_node(state: Dict) -> Dict:
        gs = GraphState(**state)
        gs.retry_count += 1
        # Do NOT clear errors here, as they may contain Summarizer feedback
        return dataclasses.asdict(gs)

    def check_planner(state: Dict) -> str:
        gs = GraphState(**state)
        # If no plan or planner errors, retry
        if not gs.plan or any("Planner" in e for e in gs.errors):
            if gs.retry_count < 3:
                return "retry"
            else:
                return "end"
        return "ok"

    def check_validation(state: Dict) -> str:
        gs = GraphState(**state)
        if gs.errors:
            # Check for terminal errors (Security Violations)
            if any("Security Violation" in e for e in gs.errors):
                return "end"
                
            if gs.retry_count < 3:
                return "retry"
            else:
                return "end"
        return "ok"

    from nl2sql.nodes.summarizer.node import SummarizerNode
    summarizer = SummarizerNode(llm=node_llm("summarizer"))

    graph.add_node("intent", _wrap_graphstate(intent, "intent"))
    graph.add_node("schema", _wrap_graphstate(schema_node, "schema"))
    graph.add_node("planner", _wrap_graphstate(planner, "planner"))
    graph.add_node("planner_retry", planner_retry_node)
    graph.add_node("sql_generator", _wrap_graphstate(generator, "sql_generator"))
    graph.add_node("validator", _wrap_graphstate(validator, "validator"))
    graph.add_node("retry_handler", retry_node)
    graph.add_node("summarizer", _wrap_graphstate(summarizer, "summarizer"))
    
    if execute:
        graph.add_node("executor", _wrap_graphstate(ExecutorNode(profile=profile), "executor"))

    graph.set_entry_point("intent")
    graph.add_edge("intent", "schema")
    graph.add_edge("schema", "planner")
    
    # Check if Planner produced a plan at all
    graph.add_conditional_edges(
        "planner",
        check_planner,
        {
            "ok": "validator",
            "retry": "summarizer", # Was planner_retry
            "end": END
        }
    )
    # Summarizer -> Planner Retry -> Planner
    graph.add_edge("summarizer", "planner_retry")
    graph.add_edge("planner_retry", "planner")

    # Validator checks the Plan
    graph.add_conditional_edges(
        "validator",
        check_validation,
        {
            "retry": "retry_handler",
            "ok": "sql_generator",
            "end": END
        }
    )
    # If validation fails, retry Planner (with feedback in state.errors)
    # Reroute through Summarizer
    graph.add_edge("retry_handler", "summarizer")
    
    if execute:
        graph.add_edge("sql_generator", "executor")
        graph.add_edge("executor", END)
    else:
        graph.add_edge("sql_generator", END)

    return graph.compile()


def run_with_graph(profile: DatasourceProfile, user_query: str, llm: Optional[LLMCallable] = None, llm_map: Optional[Dict[str, LLMCallable]] = None, execute: bool = True, vector_store: Optional[SchemaVectorStore] = None, debug: bool = False, on_thought: Optional[Callable[[str, list[str]], None]] = None) -> Dict:
    g = build_graph(profile, llm=llm, llm_map=llm_map, execute=execute, vector_store=vector_store)
    initial_state = dataclasses.asdict(
        GraphState(
            user_query=user_query,
            validation={"capabilities": get_capabilities(profile.engine).dialect},
        )
    )
    
    import time
    start_total = time.perf_counter()
    
    # Map graph node names to thoughts keys
    node_map = {
        "intent": "intent",
        "schema": "schema",
        "planner": "planner",
        "validator": "validator",
        "sql_generator": "generator",
        "summarizer": "summarizer"
    }

    if debug or on_thought:
        if debug:
            print("\n--- Starting Graph Execution (Debug Mode) ---")
        
        final_state = initial_state
        # Use stream_mode=["updates", "messages"] to get node-specific updates and token chunks
        for mode, payload in g.stream(initial_state, stream_mode=["updates", "messages"]):
            if mode == "updates":
                # Payload is {node_name: state_update}
                # This is much cleaner than "values" mode!
                for node_name, state_update in payload.items():
                    if debug:
                        print(f"\n--- Node: {node_name} ---")
                        print(json.dumps(state_update, indent=2, default=str))
                    
                    # Update final state
                    final_state.update(state_update)

                    # Trigger on_thought for node completion (logs/reasoning)
                    # This handles non-streaming nodes or the final block of streaming nodes
                    if on_thought:
                        thought_key = node_map.get(node_name)
                        if thought_key:
                            thoughts = state_update.get("thoughts", {}).get(thought_key)
                            if thoughts:
                                on_thought(thought_key, thoughts)

            elif mode == "messages":
                # Payload is (chunk, metadata)
                chunk, metadata = payload
                # metadata contains "langgraph_node"
                node_name = metadata.get("langgraph_node", "")
                thought_key = node_map.get(node_name)
                
                if thought_key and on_thought:
                    # Check if it's content
                    if hasattr(chunk, "content") and chunk.content:
                        # Send token
                        on_thought(thought_key, [chunk.content], token=True)
        
        if debug:
            print("\n--- Graph Execution Complete ---\n")
        result = final_state
    else:
        result = g.invoke(initial_state)
    
    total_duration = time.perf_counter() - start_total
    # Result is a dict, need to update latency inside it
    if "latency" not in result:
        result["latency"] = {}
    result["latency"]["total"] = total_duration
    
    return result
