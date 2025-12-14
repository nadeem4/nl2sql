from __future__ import annotations

import time
from typing import Dict, Any, Optional
from langchain_core.callbacks import BaseCallbackHandler
from nl2sql.metrics import LATENCY_LOG
from nl2sql.logger import get_logger
from nl2sql.tracing import span, get_tracer

logger = get_logger("observability")

from nl2sql.context import current_datasource_id

class ObservabilityCallback(BaseCallbackHandler):
    """
    Callback to track node execution latency and lifecycle events via LangGraph metadata.
    Also manages datasource context for proper cost attribution.
    Does NOT require modifying node code.
    """
    def __init__(self):
        self.starts: Dict[str, float] = {}
        self.tokens: Dict[str, Any] = {}
        self.tracer = get_tracer()

    def on_chain_start(self, serialized: Dict[str, Any], inputs: Dict[str, Any], **kwargs: Any) -> Any:
        metadata = kwargs.get("metadata", {})
        node_name = metadata.get("langgraph_node")
        run_name = kwargs.get("name")
        run_id = kwargs.get("run_id")

        if node_name and run_name == node_name:
            self.starts[str(run_id)] = time.perf_counter()
            
            # Handle Context
            ds_id = inputs.get("datasource_id")
            if ds_id:
                # Handle set/list/str variations commonly found in our state
                if isinstance(ds_id, (set, list)) and len(ds_id) > 0:
                    val = next(iter(ds_id))
                elif isinstance(ds_id, str):
                    val = ds_id
                else:
                    val = None
                
                if val:
                    token = current_datasource_id.set(val)
                    self.tokens[str(run_id)] = token

    def on_chain_end(self, outputs: Dict[str, Any], **kwargs: Any) -> Any:
        metadata = kwargs.get("metadata", {})
        node_name = metadata.get("langgraph_node")
        run_name = kwargs.get("name")
        run_id = kwargs.get("run_id")
        run_key = str(run_id)

        if node_name and run_name == node_name:
            start_time = self.starts.pop(run_key, None)
            if start_time:
                duration = time.perf_counter() - start_time
                LATENCY_LOG.append({
                    "node": node_name,
                    "duration": duration
                })
                logger.info(f"Node {node_name} completed", extra={"duration": duration})
            
            # Reset Context
            token = self.tokens.pop(run_key, None)
            if token:
                current_datasource_id.reset(token)

    def on_chain_error(self, error: BaseException, **kwargs: Any) -> Any:
        metadata = kwargs.get("metadata", {})
        node_name = metadata.get("langgraph_node")
        run_name = kwargs.get("name")
        run_id = kwargs.get("run_id")
        run_key = str(run_id)

        if node_name and run_name == node_name:
            start_time = self.starts.pop(run_key, None)
            if start_time:
                duration = time.perf_counter() - start_time
                LATENCY_LOG.append({
                    "node": node_name,
                    "duration": duration,
                    "error": str(error)
                })
                logger.error(f"Node {node_name} failed: {error}", extra={"duration": duration})

            # Reset Context
            token = self.tokens.pop(run_key, None)
            if token:
                current_datasource_id.reset(token)
