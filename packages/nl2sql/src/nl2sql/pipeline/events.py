"""A run as it happens: the events ``stream_query`` yields.

:class:`RunEvents` is a LangChain callback handler, passed to the graph like
the timing and usage callbacks. It turns what the graph reports into
:class:`QueryEvent` values:

``node_started``    ``{"node"}``: a pipeline step began (an SQL agent step
                    once per sub-query and again on a retry)
``node_finished``   ``{"node", "seconds", "ok"}``: it ended; ``ok`` is False
                    when it raised
``result_manifest`` ``{"trace_id", "results"}``: the aggregator saved the
                    run's results; ``trace_id`` is the ``request_id`` the
                    paging and CSV routes take
``answer_delta``    ``{"field", "text"}``: the next piece of the answer's
                    ``summary`` or ``content``, as the answer model writes it

``stream_query`` adds the terminal event, exactly one of ``final`` (the
``QueryResult``, as ``run_query`` returns it) or ``error``.
"""
from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict, Literal, Optional, Tuple
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.utils.json import parse_partial_json
from pydantic import BaseModel, Field

EventType = Literal["node_started", "node_finished", "result_manifest", "answer_delta", "final", "error"]

# The graph node whose model call writes the answer, and the answer fields a
# reader sees grow.
ANSWER_NODE = "answer_synthesizer"
ANSWER_FIELDS = ("summary", "content")


class QueryEvent(BaseModel):
    """One step of a streamed run: its ``type`` and a JSON-ready ``data`` dict."""

    type: EventType
    data: Dict[str, Any] = Field(default_factory=dict)

    def sse(self) -> str:
        """The event as one Server-Sent Event: its type as ``event``, its data as JSON."""
        return f"event: {self.type}\ndata: {json.dumps(self.data, default=str)}\n\n"


def _get(source: Any, name: str) -> Any:
    if isinstance(source, dict):
        return source.get(name)
    return getattr(source, name, None)


class _AnswerText:
    """The answer model's tool-call arguments so far, and how much of each field was sent."""

    def __init__(self) -> None:
        self.raw = ""
        self.sent: Dict[str, str] = {}

    def add(self, piece: str):
        """The new text of each answer field once ``piece`` is added: ``(field, text)`` pairs."""
        self.raw += piece
        try:
            parsed = parse_partial_json(self.raw)
        except ValueError:  # nothing parseable yet
            return []
        if not isinstance(parsed, dict):
            return []
        out = []
        for field in ANSWER_FIELDS:
            value, before = parsed.get(field), self.sent.get(field, "")
            # A value that does not extend what was sent (an escape cut in
            # half reads differently once complete) waits for the next piece.
            if isinstance(value, str) and len(value) > len(before) and value.startswith(before):
                self.sent[field] = value
                out.append((field, value[len(before):]))
        return out


def _pieces(chunk: Any, token: str) -> str:
    """What one streamed chunk adds to the answer: its tool-call arguments, else its text."""
    message = getattr(chunk, "message", None)
    calls = getattr(message, "tool_call_chunks", None) or []
    args = "".join(c.get("args") or "" for c in calls)
    if args:
        return args
    content = getattr(message, "content", None)
    if isinstance(content, str) and content:
        return content
    # Anthropic's chunks carry content blocks, not a string; a tool call's
    # pieces are in ``tool_call_chunks`` above.
    return token if isinstance(token, str) else ""


class RunEvents(BaseCallbackHandler):
    """Turns a run's callbacks into :class:`QueryEvent` values, handed to ``emit``.

    A node's own run is the one whose name is its ``langgraph_node``; the
    runnables inside it (prompts, models, parsers, routers) carry the same tag
    under other names and are not events. ``emit`` is called on the graph's
    worker threads.
    """

    def __init__(self, emit: Callable[[QueryEvent], None]) -> None:
        self._emit = emit
        self._nodes: Dict[UUID, Tuple[str, float]] = {}
        self._answers: Dict[UUID, _AnswerText] = {}
        self.trace_id: Optional[str] = None

    def on_chain_start(self, serialized: Optional[Dict[str, Any]], inputs: Any, *, run_id: UUID,
                       parent_run_id: Optional[UUID] = None, **kwargs: Any) -> None:
        if parent_run_id is None and self.trace_id is None and isinstance(inputs, dict):
            self.trace_id = inputs.get("trace_id")
        node = (kwargs.get("metadata") or {}).get("langgraph_node")
        if node and kwargs.get("name") == node:
            self._nodes[run_id] = (node, time.perf_counter())
            self._emit(QueryEvent(type="node_started", data={"node": node}))

    def _finish(self, run_id: UUID, ok: bool, outputs: Any = None) -> None:
        started = self._nodes.pop(run_id, None)
        if started is None:
            return
        node, t0 = started
        self._emit(QueryEvent(type="node_finished", data={
            "node": node, "seconds": round(time.perf_counter() - t0, 4), "ok": ok}))
        if node == "aggregator":
            results = _get(_get(outputs, "aggregator_response"), "results")
            if results is not None:
                self._emit(QueryEvent(type="result_manifest", data={
                    "trace_id": self.trace_id or "",
                    "results": [r.model_dump(mode="json") if hasattr(r, "model_dump") else r for r in results],
                }))

    def on_chain_end(self, outputs: Any, *, run_id: UUID, **kwargs: Any) -> None:
        self._finish(run_id, True, outputs)

    def on_chain_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        self._finish(run_id, False)

    def on_chat_model_start(self, serialized: Any, messages: Any, *, run_id: UUID, **kwargs: Any) -> None:
        if (kwargs.get("metadata") or {}).get("langgraph_node") == ANSWER_NODE:
            self._answers[run_id] = _AnswerText()

    def on_llm_new_token(self, token: str, *, chunk: Any = None, run_id: UUID, **kwargs: Any) -> None:
        answer = self._answers.get(run_id)
        if answer is None:
            return
        for field, text in answer.add(_pieces(chunk, token)):
            self._emit(QueryEvent(type="answer_delta", data={"field": field, "text": text}))

    def on_llm_end(self, response: Any, *, run_id: UUID, **kwargs: Any) -> None:
        self._answers.pop(run_id, None)

    on_llm_error = on_llm_end
