from typing import Callable, Dict, Optional
import dataclasses
import time
from nl2sql.schemas import GraphState
from nl2sql.tracing import span
from nl2sql.logger import get_logger

def wrap_graphstate(fn: Callable[[GraphState], GraphState], name: Optional[str] = None):
    """
    Wraps a function that operates on GraphState to work with LangGraph's dict-based state.

    Handles:
    - Conversion from dict to GraphState and back.
    - Logging of node execution start/end.
    - Tracing (spans).
    - Latency measurement.

    Args:
        fn: The function to wrap (takes GraphState, returns GraphState).
        name: Optional name for the node (for logging/tracing).

    Returns:
        A wrapped function that accepts and returns a dictionary.
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
