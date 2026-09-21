"""``TraceRecorder``: a LangChain callback that records every node execution of a run.

It attributes work to nodes the way ``NodeTimingCallback`` and
``TokenUsageCallback`` do, from ``metadata["langgraph_node"]``, which LangGraph
puts on every run inside a node. A chain run *is* a node execution when its name
equals that node name and it is not already inside an execution of the same
node; every other run (prompt templates, parsers, routers, the subgraph) is
attributed to the execution it happens inside.

Each execution gets:

* ``sub_query_id`` -- inherited from the enclosing ``sql_agent`` dispatch, whose
  input carries ``subgraph_id = "sql_agent:<sub_query_id>:<trace_id>"``;
* ``attempt`` -- 1, 2, ... per ``(node, sub_query_id)``, so a retried planner is
  attempt 2 even though it answers the same question;
* the state fields the node reads (``inputs``) and the update it returns
  (``outputs``), errors, warnings and any exception;
* its LLM calls, each keyed ``(node, sub_query_id, attempt, call_index)`` with
  the exact messages sent, the invocation parameters, the raw response, the
  parsed result and -- from ``TokenUsageCallback``, not a second reader -- usage.

Sub-queries run in parallel, hence the lock. The key of the LLM call a thread is
making right now is kept thread-locally for :mod:`nl2sql.tracing.replay`.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler

from nl2sql.tracing.document import jsonable

# The state fields each node reads. What is not listed is not recorded as an
# input (the node's own output already shows what it produced).
NODE_INPUTS: Dict[str, Tuple[str, ...]] = {
    "datasource_resolver": ("user_query", "datasource_id", "user_context"),
    "decomposer": ("user_query", "datasource_resolver_response"),
    "global_planner": ("decomposer_response",),
    "layer_router": ("artifact_refs",),
    "sql_agent": ("subgraph_id",),
    "aggregator": ("global_planner_response", "artifact_refs"),
    "answer_synthesizer": ("user_query", "aggregator_response"),
    "schema_retriever": ("sub_query",),
    "ast_planner": ("sub_query", "relevant_tables", "errors", "retry_count"),
    "logical_validator": ("sub_query", "ast_planner_response", "user_context"),
    "generator": ("ast_planner_response",),
    "executor": ("sub_query", "generator_response", "user_context"),
    "retry_handler": ("retry_count",),
    "refiner": ("sub_query", "ast_planner_response", "errors", "retry_count"),
}

# LLM invocation parameters worth keeping; credentials never appear here.
_PARAMS = ("model", "model_name", "temperature", "seed", "max_tokens", "top_p", "stop",
           "tool_choice", "tools", "response_format", "parallel_tool_calls", "n", "stream")

_BLOCKING = {"ERROR", "CRITICAL"}


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _get(source: Any, name: str) -> Any:
    if isinstance(source, dict):
        return source.get(name)
    return getattr(source, name, None)


def _sub_query_from(inputs: Any) -> Optional[str]:
    subgraph_id = _get(inputs, "subgraph_id")
    if isinstance(subgraph_id, str) and subgraph_id.count(":") >= 2:
        return subgraph_id.split(":")[1]
    return None


def _role(message: Any) -> str:
    kind = getattr(message, "type", "") or ""
    return {"human": "user", "ai": "assistant", "system": "system", "tool": "tool"}.get(kind, kind or "user")


def message_dicts(messages: List[Any]) -> List[Dict[str, Any]]:
    """The chat messages as sent: role, content and any tool calls."""
    out = []
    for message in messages or []:
        entry: Dict[str, Any] = {"role": _role(message), "content": jsonable(getattr(message, "content", message))}
        tool_calls = getattr(message, "tool_calls", None)
        if tool_calls:
            entry["tool_calls"] = jsonable(tool_calls)
        out.append(entry)
    return out


def prompt_sha256(messages: List[Dict[str, Any]]) -> str:
    """A stable digest of role + content, used to tell a changed prompt from a recorded one."""
    canonical = [{"role": m.get("role"), "content": m.get("content")} for m in messages or []]
    return hashlib.sha256(json.dumps(canonical, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _response_dict(response: Any) -> Dict[str, Any]:
    """The raw model answer: text content, tool calls (as the provider sent them) and finish reason."""
    for generations in getattr(response, "generations", None) or []:
        for generation in generations:
            message = getattr(generation, "message", None)
            if message is None:
                return {"content": getattr(generation, "text", "")}
            meta = getattr(message, "response_metadata", None) or {}
            out: Dict[str, Any] = {
                "content": jsonable(message.content),
                "tool_calls": [
                    {"id": tc.get("id"), "name": tc.get("name"), "args": jsonable(tc.get("args"))}
                    for tc in (getattr(message, "tool_calls", None) or [])
                ],
                "finish_reason": meta.get("finish_reason"),
                "model_name": meta.get("model_name"),
            }
            invalid = getattr(message, "invalid_tool_calls", None)
            if invalid:
                out["invalid_tool_calls"] = jsonable(invalid)
            refusal = (getattr(message, "additional_kwargs", None) or {}).get("refusal")
            if refusal:
                out["refusal"] = refusal
            return out
    return {}


def _errors(outputs: Any) -> Tuple[List[Any], List[Any]]:
    """Splits the errors a node returned into blocking ones and warnings."""
    errors, warnings = [], []
    for error in (_get(outputs, "errors") or []) if outputs is not None else []:
        item = jsonable(error)
        severity = str((item or {}).get("severity", "ERROR") if isinstance(item, dict) else "ERROR").upper()
        (errors if severity in _BLOCKING else warnings).append(item)
    for warning in (_get(outputs, "warnings") or []) if outputs is not None else []:
        warnings.append(jsonable(warning))
    return errors, warnings


@dataclass
class _LLMCall:
    run_id: UUID
    key: Dict[str, Any]
    messages: List[Dict[str, Any]]
    params: Dict[str, Any]
    started_at: str
    t0: float
    response: Optional[Dict[str, Any]] = None
    parsed: Any = None
    has_parsed: bool = False
    error: Optional[str] = None
    duration_s: Optional[float] = None


@dataclass
class _Execution:
    seq: int
    node: str
    sub_query_id: Optional[str]
    attempt: int
    parent: Optional[str]
    inputs: Dict[str, Any]
    started_at: str
    t0: float
    ended_at: Optional[str] = None
    duration_s: Optional[float] = None
    end_seq: Optional[int] = None
    outputs: Any = None
    errors: List[Any] = field(default_factory=list)
    warnings: List[Any] = field(default_factory=list)
    exception: Optional[str] = None
    llm_calls: List[_LLMCall] = field(default_factory=list)


@dataclass
class _Run:
    execution: Optional[_Execution]
    sub_query_id: Optional[str]
    is_node: bool = False


class TraceRecorder(BaseCallbackHandler):
    """Records node executions and their LLM calls for one run."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._runs: Dict[UUID, _Run] = {}
        self._executions: List[_Execution] = []
        self._attempts: Dict[Tuple[str, Optional[str]], int] = {}
        self._calls: Dict[UUID, _LLMCall] = {}
        # parent chain run -> the LLM call whose parsed result it will return
        self._awaiting_parse: Dict[UUID, _LLMCall] = {}
        self._seq = 0
        self._local = threading.local()

    # -- chains (nodes) --------------------------------------------------
    def on_chain_start(self, serialized, inputs, *, run_id: UUID, parent_run_id: Optional[UUID] = None,
                       metadata: Optional[Dict[str, Any]] = None, **kwargs: Any) -> None:
        node = (metadata or {}).get("langgraph_node")
        name = kwargs.get("name") or (serialized or {}).get("name")
        with self._lock:
            parent = self._runs.get(parent_run_id) if parent_run_id else None
            execution = parent.execution if parent else None
            sub_query_id = parent.sub_query_id if parent else None
            if node and name == node and (execution is None or execution.node != node):
                sub_query_id = _sub_query_from(inputs) or sub_query_id
                key = (node, sub_query_id)
                self._attempts[key] = self._attempts.get(key, 0) + 1
                self._seq += 1
                execution = _Execution(
                    seq=self._seq, node=node, sub_query_id=sub_query_id, attempt=self._attempts[key],
                    parent=execution.node if execution else None,
                    inputs=self._select_inputs(node, inputs),
                    started_at=_now(), t0=time.perf_counter(),
                )
                self._executions.append(execution)
                self._runs[run_id] = _Run(execution, sub_query_id, is_node=True)
                return
            self._runs[run_id] = _Run(execution, sub_query_id)

    @staticmethod
    def _select_inputs(node: str, inputs: Any) -> Dict[str, Any]:
        fields = NODE_INPUTS.get(node)
        if fields is None:
            data = inputs if isinstance(inputs, dict) else (jsonable(inputs) if inputs is not None else {})
            return {k: jsonable(v) for k, v in (data or {}).items() if v not in (None, [], {}, "")} \
                if isinstance(data, dict) else {"value": jsonable(data)}
        return {name: jsonable(_get(inputs, name)) for name in fields if _get(inputs, name) not in (None, [], {}, "")}

    def on_chain_end(self, outputs, *, run_id: UUID, **kwargs: Any) -> None:
        with self._lock:
            call = self._awaiting_parse.pop(run_id, None)
            if call is not None and not call.has_parsed:
                call.parsed, call.has_parsed = jsonable(outputs), True
            run = self._runs.get(run_id)
            if run is None or not run.is_node:
                return
            execution = run.execution
            self._close(execution)
            execution.outputs = jsonable(outputs)
            execution.errors, execution.warnings = _errors(outputs if isinstance(outputs, dict) else None)

    def on_chain_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        with self._lock:
            self._awaiting_parse.pop(run_id, None)
            run = self._runs.get(run_id)
            if run is None or not run.is_node:
                return
            self._close(run.execution)
            run.execution.exception = f"{type(error).__name__}: {error}"

    def _close(self, execution: _Execution) -> None:
        self._seq += 1
        execution.end_seq = self._seq
        execution.ended_at = _now()
        execution.duration_s = round(time.perf_counter() - execution.t0, 4)

    # -- LLM calls -------------------------------------------------------
    def _open_call(self, run_id: UUID, parent_run_id: Optional[UUID], messages: List[Dict[str, Any]],
                   metadata: Optional[Dict[str, Any]], kwargs: Dict[str, Any]) -> None:
        params = kwargs.get("invocation_params") or {}
        kept = {k: jsonable(params[k]) for k in _PARAMS if k in params}
        with self._lock:
            parent = self._runs.get(parent_run_id) if parent_run_id else None
            execution = parent.execution if parent else None
            if execution is None:
                node = (metadata or {}).get("langgraph_node") or "unknown"
                sub_query_id = parent.sub_query_id if parent else None
                key = (node, sub_query_id)
                self._attempts[key] = self._attempts.get(key, 0) + 1
                self._seq += 1
                execution = _Execution(seq=self._seq, node=node, sub_query_id=sub_query_id,
                                       attempt=self._attempts[key], parent=None, inputs={},
                                       started_at=_now(), t0=time.perf_counter())
                self._executions.append(execution)
            call = _LLMCall(
                run_id=run_id,
                key={"node": execution.node, "sub_query_id": execution.sub_query_id,
                     "attempt": execution.attempt, "call_index": len(execution.llm_calls) + 1},
                messages=messages, params=kept, started_at=_now(), t0=time.perf_counter(),
            )
            execution.llm_calls.append(call)
            self._calls[run_id] = call
            self._runs[run_id] = _Run(execution, execution.sub_query_id)
            if parent_run_id is not None:
                self._awaiting_parse[parent_run_id] = call
        self._local.key = dict(call.key)

    def on_chat_model_start(self, serialized, messages, *, run_id: UUID, parent_run_id: Optional[UUID] = None,
                            metadata=None, **kwargs: Any) -> None:
        self._open_call(run_id, parent_run_id, message_dicts(messages[0] if messages else []), metadata, kwargs)

    def on_llm_start(self, serialized, prompts, *, run_id: UUID, parent_run_id: Optional[UUID] = None,
                     metadata=None, **kwargs: Any) -> None:
        self._open_call(run_id, parent_run_id, [{"role": "user", "content": p} for p in prompts or []],
                        metadata, kwargs)

    def on_llm_end(self, response, *, run_id: UUID, **kwargs: Any) -> None:
        with self._lock:
            call = self._calls.get(run_id)
            if call is not None:
                call.response = _response_dict(response)
                call.duration_s = round(time.perf_counter() - call.t0, 4)

    def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        with self._lock:
            call = self._calls.get(run_id)
            if call is not None:
                call.error = f"{type(error).__name__}: {error}"
                call.duration_s = round(time.perf_counter() - call.t0, 4)

    def current_call_key(self) -> Optional[Dict[str, Any]]:
        """The key of the LLM call this thread started most recently."""
        return getattr(self._local, "key", None)

    # -- output ----------------------------------------------------------
    def node_executions(self, usage=None) -> List[Dict[str, Any]]:
        """Every execution so far, in start order, as plain JSON.

        ``usage`` is the run's ``TokenUsageCallback``; each LLM call's usage is
        its record for the same LangChain run id.
        """
        with self._lock:
            executions = list(self._executions)
        out = []
        for e in executions:
            if e.exception:
                status = "error"
            elif e.ended_at is None:
                status = "unfinished"
            elif e.errors or any(c.error for c in e.llm_calls):
                status = "error"
            elif e.warnings:
                status = "warning"
            else:
                status = "ok"
            out.append({
                "seq": e.seq,
                "end_seq": e.end_seq,
                "node": e.node,
                "sub_query_id": e.sub_query_id,
                "attempt": e.attempt,
                "parent": e.parent,
                "status": status,
                "started_at": e.started_at,
                "ended_at": e.ended_at,
                "duration_s": e.duration_s,
                "inputs": e.inputs,
                "outputs": e.outputs,
                "errors": e.errors,
                "warnings": e.warnings,
                "exception": e.exception,
                "llm_calls": [self._call_dict(c, usage) for c in e.llm_calls],
            })
        return out

    @staticmethod
    def _call_dict(call: _LLMCall, usage) -> Dict[str, Any]:
        record = usage.call_for(call.run_id) if usage is not None else None
        return {
            "key": call.key,
            "model": (record.model if record else "") or call.params.get("model_name") or call.params.get("model") or "",
            "params": call.params,
            "messages": call.messages,
            "prompt_sha256": prompt_sha256(call.messages),
            "response": call.response,
            "parsed": call.parsed,
            "error": call.error,
            "started_at": call.started_at,
            "duration_s": call.duration_s,
            "usage": record.model_dump(mode="json") if record else None,
        }
