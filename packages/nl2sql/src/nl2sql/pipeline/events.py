"""A run as it happens: the step events ``RunEvents`` publishes on the run's scope.

:class:`RunEvents` is a LangChain callback handler. ``QueryAPI.open_scope``
attaches one to every run's :class:`~nl2sql.events.RunScope`, so the graph
gets it with the scope's callbacks, and it publishes, through the scope:

``node.started``    ``{"node", "sub_query_id"}``: a pipeline step began (an
                    SQL agent step once per sub-query and again on a retry)
``node.finished``   ``{"node", "sub_query_id", "seconds", "ok"}``: it ended;
                    ``ok`` is False when it raised
``result.manifest`` ``{"results"}``: the aggregator saved the run's results;
                    the event's ``trace_id`` is the ``request_id`` the paging
                    and CSV routes take
``answer.delta``    ``{"field", "text"}``: the next piece of the answer's
                    ``summary`` or ``content``, as the answer model writes it
                    (only a streamed run's answer model streams)

``sub_query_id`` is None for a step outside a sub-query. ``stream_query``
yields these, with the scope's own ``run.*`` events, from the caller-safe
allow-list (``nl2sql.events.STREAM_EVENTS``). See docs/architecture/wave_a.md,
section 3.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, Mapping, Optional, Tuple
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.utils.json import parse_partial_json

from nl2sql.tracing.recorder import sub_query_from

# The graph node whose model call writes the answer, and the answer fields a
# reader sees grow.
ANSWER_NODE = "answer_synthesizer"
ANSWER_FIELDS = ("summary", "content")

Publish = Callable[[str, Mapping[str, Any]], Any]


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
    """Publishes a run's steps, its result manifest and its answer as it is written, through ``publish``.

    ``publish(name, data)`` is the run scope's (``RunScope.publish``). A
    node's own run is the one whose name is its ``langgraph_node``; the
    runnables inside it (prompts, models, parsers, routers) carry the same tag
    under other names and are not events. A run inherits its parent's
    sub-query, and a sub-query's subgraph names it in its ``subgraph_id``.
    Called on the graph's worker threads.
    """

    def __init__(self, publish: Publish) -> None:
        self._publish = publish
        self._nodes: Dict[UUID, Tuple[str, Optional[str], float]] = {}
        self._sub_queries: Dict[UUID, Optional[str]] = {}
        self._answers: Dict[UUID, _AnswerText] = {}

    def on_chain_start(self, serialized: Optional[Dict[str, Any]], inputs: Any, *, run_id: UUID,
                       parent_run_id: Optional[UUID] = None, **kwargs: Any) -> None:
        inherited = self._sub_queries.get(parent_run_id) if parent_run_id is not None else None
        sub_query_id = sub_query_from(inputs) or inherited
        self._sub_queries[run_id] = sub_query_id
        node = (kwargs.get("metadata") or {}).get("langgraph_node")
        if node and kwargs.get("name") == node:
            self._nodes[run_id] = (node, sub_query_id, time.perf_counter())
            self._publish("node.started", {"node": node, "sub_query_id": sub_query_id})

    def _finish(self, run_id: UUID, ok: bool, outputs: Any = None) -> None:
        self._sub_queries.pop(run_id, None)
        started = self._nodes.pop(run_id, None)
        if started is None:
            return
        node, sub_query_id, t0 = started
        self._publish("node.finished", {"node": node, "sub_query_id": sub_query_id,
                                        "seconds": round(time.perf_counter() - t0, 4), "ok": ok})
        if node == "aggregator":
            results = _get(_get(outputs, "aggregator_response"), "results")
            if results is not None:
                self._publish("result.manifest", {
                    "results": [r.model_dump(mode="json") if hasattr(r, "model_dump") else r for r in results]})

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
            self._publish("answer.delta", {"field": field, "text": text})

    def on_llm_end(self, response: Any, *, run_id: UUID, **kwargs: Any) -> None:
        self._answers.pop(run_id, None)

    on_llm_error = on_llm_end
