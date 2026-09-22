"""Replays a run trace: the real pipeline, with every LLM answer taken from the recording.

How it works:

* The engine's chat clients are swapped for ``ChatOpenAI`` clients whose HTTP
  transport is :class:`TracePlayback` -- an in-process ``httpx`` transport. No
  request leaves the process, so replay makes zero model calls. Everything from
  the request the client builds to how the node parses the answer is the
  engine's own code, exactly as in a live run.
* Playback is keyed by ``(node, sub_query_id, attempt, call_index)``, never by
  question text: a retried planner asks the same question twice, and parallel
  sub-queries interleave. A second ``TraceRecorder`` rides along on the replayed
  run and says, thread by thread, which key the call being made has.
* A call the recording does not have, or whose prompt differs from the recorded
  one, is a *divergence*. The first one is kept, the run is cancelled, and the
  report names the node, sub-query and attempt where it happened.

The request is classified and answered with the fake server's own helpers
(:func:`nl2sql.testing.fake_llm.classify_request` and
:func:`nl2sql.testing.fake_llm.completion`).

Limits: replay exercises the code downstream of the model. After a prompt
change the recorded answer is stale and a real run is needed. The datasource
must be reachable, and question embedding for datasource resolution still uses
the configured embedding provider.
"""
from __future__ import annotations

import difflib
import json
import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import httpx

from nl2sql.common.cancellation import CancellationToken
from nl2sql.testing.fake_llm import classify_request, completion
from nl2sql.tracing.document import Limits, Redactor, cap
from nl2sql.tracing.recorder import TraceRecorder, prompt_sha256
from nl2sql.tracing.trace import collect_secrets

__all__ = ["Divergence", "TracePlayback", "ReplayReport", "diff_results", "prompt_sha256", "replay_trace",
           "unreachable_datasources"]

Key = Tuple[str, Optional[str], int, int]


def _key(key: Dict[str, Any]) -> Key:
    return (key.get("node"), key.get("sub_query_id"), int(key.get("attempt") or 0), int(key.get("call_index") or 0))


@dataclass
class Divergence:
    node: str
    sub_query_id: Optional[str]
    attempt: int
    call_index: int
    reason: str  # "missing" | "prompt_changed" | "unused"
    detail: str = ""

    def where(self) -> str:
        sub = f", sub-query {self.sub_query_id}" if self.sub_query_id else ""
        return f"{self.node}{sub}, attempt {self.attempt}, LLM call {self.call_index}"

    def describe(self) -> str:
        return {
            "missing": "the replayed run asked for an LLM call the recording does not have",
            "prompt_changed": "the prompt differs from the recorded one, so the recorded answer no longer applies",
            "unused": "the replayed run finished without making this recorded LLM call",
        }.get(self.reason, self.reason)


def _openai_usage(usage: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    u = usage or {}
    return {
        "prompt_tokens": int(u.get("input_tokens") or 0),
        "completion_tokens": int(u.get("output_tokens") or 0),
        "total_tokens": int(u.get("total_tokens") or 0),
        "prompt_tokens_details": {"cached_tokens": int(u.get("cached_input_tokens") or 0)},
        "completion_tokens_details": {"reasoning_tokens": int(u.get("reasoning_tokens") or 0)},
    }


def _body_messages(body: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [{"role": m.get("role"), "content": m.get("content")} for m in body.get("messages") or []]


def _prompt_diff(recorded: List[Dict[str, Any]], replayed: List[Dict[str, Any]], limit: int = 40) -> str:
    def lines(messages):
        return [f"[{m.get('role')}] {line}" for m in messages for line in str(m.get("content")).splitlines()]

    diff = list(difflib.unified_diff(lines(recorded), lines(replayed), "recorded prompt", "replayed prompt",
                                     lineterm="", n=1))
    if len(diff) > limit:
        diff = diff[:limit] + [f"... {len(diff) - limit} more diff lines"]
    return "\n".join(diff)


class TracePlayback(httpx.BaseTransport):
    """Answers chat-completion requests from a trace's recorded LLM calls."""

    def __init__(self, trace: Dict[str, Any]):
        self._calls: Dict[Key, Dict[str, Any]] = {}
        for node in trace.get("nodes") or []:
            for call in node.get("llm_calls") or []:
                self._calls[_key(call["key"])] = call
        self._used: set = set()
        self._lock = threading.Lock()
        self.served = 0
        self.divergence: Optional[Divergence] = None
        self.recorder = TraceRecorder()
        self.token = CancellationToken()

    def _diverge(self, key: Dict[str, Any], reason: str, detail: str = "") -> None:
        with self._lock:
            if self.divergence is None:
                k = _key(key)
                self.divergence = Divergence(k[0], k[1], k[2], k[3], reason, detail)
        self.token.cancel()

    def respond(self, key: Optional[Dict[str, Any]], body: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """The recorded answer for ``key``, or None (and a divergence) if there is none."""
        key = key or {"node": "unknown", "sub_query_id": None, "attempt": 0, "call_index": 0}
        call = self._calls.get(_key(key))
        if call is None:
            self._diverge(key, "missing")
            return None
        replayed = _body_messages(body)
        if call.get("prompt_sha256") and prompt_sha256(replayed) != call["prompt_sha256"]:
            self._diverge(key, "prompt_changed", _prompt_diff(call.get("messages") or [], replayed))
            return None
        response = call.get("response") or {}
        mode, name, _text = classify_request(body)
        if mode == "tools":
            tool_calls = response.get("tool_calls") or []
            invalid = response.get("invalid_tool_calls") or []
            payload = (tool_calls[0].get("args") if tool_calls
                       else (invalid[0].get("args") if invalid else response.get("content")))
            name = (tool_calls[0].get("name") if tool_calls else None) or name
        else:
            payload = response.get("content") or ""
        with self._lock:
            self._used.add(_key(key))
            self.served += 1
        return completion(body, mode, name, payload, _openai_usage(call.get("usage")))

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        answer = self.respond(self.recorder.current_call_key(), body)
        if answer is None:
            where = self.divergence.where() if self.divergence else "unknown"
            return httpx.Response(400, json={"error": {"message": f"trace replay diverged at {where}"}},
                                  request=request)
        return httpx.Response(200, json=answer, request=request)

    def unused_calls(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [call["key"] for k, call in self._calls.items() if k not in self._used]


# Fields that differ on every run by construction.
_VOLATILE = {"trace_id", "trace_path", "timings", "usage", "artifact_refs", "reasoning"}


def diff_results(recorded: Dict[str, Any], replayed: Dict[str, Any]) -> List[str]:
    """Human-readable differences between two ``QueryResult`` dumps, ignoring ids and timings."""
    changes: List[str] = []

    def walk(a: Any, b: Any, path: str) -> None:
        if isinstance(a, dict) and isinstance(b, dict):
            for k in sorted(set(a) | set(b)):
                if not path and k in _VOLATILE:
                    continue
                walk(a.get(k), b.get(k), f"{path}.{k}" if path else k)
        elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
            for i, (x, y) in enumerate(zip(a, b)):
                walk(x, y, f"{path}[{i}]")
        elif a != b:
            changes.append(f"{path}: recorded {json.dumps(a, default=str)[:200]} -> replayed "
                           f"{json.dumps(b, default=str)[:200]}")

    walk(recorded or {}, replayed or {}, "")
    return changes


def _trace_datasources(doc: Dict[str, Any]) -> List[str]:
    ids = []
    request_id = (doc.get("request") or {}).get("datasource_id")
    if request_id:
        ids.append(request_id)
    for sq in (doc.get("result") or {}).get("sub_queries") or []:
        if sq.get("datasource_id") and sq["datasource_id"] not in ids:
            ids.append(sq["datasource_id"])
    return ids


def unreachable_datasources(doc: Dict[str, Any], ctx: Any) -> List[str]:
    """The trace's datasources that are not configured here or do not answer, with why."""
    problems = []
    for ds_id in _trace_datasources(doc):
        try:
            adapter = ctx.ds_registry.get_adapter(ds_id)
        except Exception:
            problems.append(f"{ds_id}: not configured in this environment")
            continue
        try:
            if not adapter.test_connection():
                problems.append(f"{ds_id}: connection test failed")
        except Exception as exc:
            problems.append(f"{ds_id}: {exc}")
    return problems


@dataclass
class ReplayReport:
    result: Dict[str, Any]
    served: int
    divergence: Optional[Divergence] = None
    differences: List[str] = field(default_factory=list)

    @property
    def reproduced(self) -> bool:
        return self.divergence is None and not self.differences


def _install_playback_clients(ctx: Any, playback: TracePlayback) -> None:
    """Points every configured agent at the playback transport instead of its provider."""
    from nl2sql.llm.wires.openai import build_chat_client

    registry = ctx.llm_registry
    http_client = httpx.Client(transport=playback)
    with registry._lock:
        for name, cfg in registry._configs.items():
            registry.llms[name] = build_chat_client(
                cfg.model, cfg.temperature, api_key="trace-replay", tags=[name],
                base_url="http://trace-replay.invalid/v1", http_client=http_client, max_retries=0,
            )


class _AfterDivergence(logging.Filter):
    def __init__(self, playback: TracePlayback):
        super().__init__()
        self._playback = playback

    def filter(self, record: logging.LogRecord) -> bool:
        return self._playback.divergence is None


def recorded_plan_cache_hit(doc: Dict[str, Any]) -> bool:
    """Whether the recorded run took any plan from the plan cache.

    A replay must make the LLM calls the recording made. When the recorded
    planner called the model, the replay turns the cache off so that call is
    replayed rather than answered from a plan cached since; when the recording
    used the cache, the replay needs it.
    """
    for node in doc.get("nodes") or []:
        if node.get("node") != "ast_planner":
            continue
        response = (node.get("outputs") or {}).get("ast_planner_response") or {}
        if isinstance(response, dict) and response.get("plan_source") == "cache":
            return True
    return False


def replay_trace(doc: Dict[str, Any], ctx: Any) -> ReplayReport:
    """Re-runs the traced question, answering every LLM call from the recording."""
    from nl2sql.api.query_api import result_from_state
    from nl2sql.auth import UserContext
    from nl2sql.common.settings import settings
    from nl2sql.pipeline.runtime import run_with_graph

    playback = TracePlayback(doc)
    _install_playback_clients(ctx, playback)
    request = doc.get("request") or {}
    user_context = UserContext(roles=list(request.get("roles") or []),
                               **({"tenant_id": request["tenant_id"]} if request.get("tenant_id") else {}))

    # Retry back-off only waits; the recorded answers do not depend on it.
    saved = {k: getattr(settings, k)
             for k in ("sql_agent_retry_base_delay_sec", "sql_agent_retry_jitter_sec", "plan_cache_enabled")}
    for k in ("sql_agent_retry_base_delay_sec", "sql_agent_retry_jitter_sec"):
        setattr(settings, k, 0.0)
    settings.plan_cache_enabled = saved["plan_cache_enabled"] and recorded_plan_cache_hit(doc)
    # Once the run has diverged, the nodes log the refused call as a failure with
    # a stack trace. That is the replay stopping, not a fault: the report says so.
    quiet = _AfterDivergence(playback)
    handlers = list(logging.getLogger().handlers)
    for handler in handlers:
        handler.addFilter(quiet)
    try:
        state = run_with_graph(
            ctx, request.get("question") or "", datasource_id=request.get("datasource_id"),
            execute=bool(request.get("execute", True)), callbacks=[playback.recorder],
            user_context=user_context, cancellation_token=playback.token, trace_mode="off",
        )
    finally:
        for k, v in saved.items():
            setattr(settings, k, v)
        for handler in handlers:
            handler.removeFilter(quiet)

    result = result_from_state(state, artifact_store=getattr(ctx, "artifact_store", None),
                               sample_rows=settings.trace_sample_rows).model_dump(mode="json")
    # Compared the way the recorded result was stored: capped, then redacted.
    limits = Limits(sample_rows=settings.trace_sample_rows, max_field_chars=settings.trace_max_field_chars)
    result = Redactor(collect_secrets(ctx)).redact(cap(result, limits))
    report = ReplayReport(result=result, served=playback.served, divergence=playback.divergence)
    if report.divergence is None:
        unused = playback.unused_calls()
        if unused:
            k = _key(unused[0])
            report.divergence = Divergence(k[0], k[1], k[2], k[3], "unused")
    if report.divergence is None:
        report.differences = diff_results(doc.get("result") or {}, result)
    return report
