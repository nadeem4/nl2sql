import functools
from typing import Callable, Any, Dict
from nl2sql.errors import PipelineError, ErrorSeverity
from nl2sql.logger import get_logger

def node_guard(node_name: str = "unknown"):
    """
    Decorator to wrap node execution in a try-except block.
    
    Catches any exceptions and returns a structured PipelineError 
    updates the graph state with usage of ErrorSeverity.CRITICAL.
    
    Args:
        node_name: The name of the node (for logging and error reporting).
    """
    def decorator(func: Callable[..., Dict[str, Any]]):
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Dict[str, Any]:
            try:
                return func(*args, **kwargs)
            except Exception as e:
                logger = get_logger(node_name)
                logger.error(f"Node {node_name} failed: {e}")
                return {
                    "errors": [
                        PipelineError(
                            node=node_name,
                            message=f"Node execution failed: {e}",
                            severity=ErrorSeverity.CRITICAL,
                            error_code=f"{node_name.upper()}_CRASH",
                            stack_trace=str(e)
                        )
                    ],
                    "reasoning": [{"node": node_name, "content": f"Crash: {e}", "type": "error"}]
                }
        return wrapper
    return decorator
