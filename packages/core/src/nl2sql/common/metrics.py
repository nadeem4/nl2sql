from typing import List, Dict, Any

TOKEN_LOG: List[Dict[str, Any]] = []
LATENCY_LOG: List[Dict[str, Any]] = []

def reset_usage():
    """Resets the token and latency logs."""
    TOKEN_LOG.clear()
    LATENCY_LOG.clear()
