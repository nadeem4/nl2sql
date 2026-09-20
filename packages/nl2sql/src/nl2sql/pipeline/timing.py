from __future__ import annotations

import time
from typing import Any, Dict, Optional, Tuple
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler


class NodeTimingCallback(BaseCallbackHandler):
    """Wall-clock seconds per LangGraph node, keyed by node name.

    LangGraph names a node's chain run after the node, but it also tags the run
    with ``metadata["langgraph_node"]``; that tag is preferred because the run
    name is not always the node name (subgraph invocations are named after the
    graph). Repeated runs of the same node -- a retry, or a node dispatched once
    per sub-query -- keep the longest observed elapsed time rather than the last.
    """

    def __init__(self) -> None:
        self.timings: Dict[str, float] = {}
        self._started: Dict[UUID, Tuple[str, float]] = {}

    @staticmethod
    def _name(serialized: Optional[Dict[str, Any]], kwargs: Dict[str, Any]) -> str:
        metadata = kwargs.get("metadata") or {}
        return (
            metadata.get("langgraph_node")
            or kwargs.get("name")
            or (serialized or {}).get("name")
            or "chain"
        )

    def on_chain_start(
        self,
        serialized: Dict[str, Any],
        inputs: Dict[str, Any],
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        self._started[run_id] = (self._name(serialized, kwargs), time.perf_counter())

    def on_chain_end(self, outputs: Any, *, run_id: UUID, **kwargs: Any) -> None:
        started = self._started.pop(run_id, None)
        if started:
            name, t0 = started
            self.timings[name] = round(
                max(self.timings.get(name, 0.0), time.perf_counter() - t0), 4
            )

    on_chain_error = on_chain_end
