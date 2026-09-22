"""Token and latency telemetry for every LLM call, rolled up per node and per question.

``TokenUsageCallback`` is the one place the engine reads token usage. It is a
LangChain callback, not a node concern, because ``with_structured_output(...)``
hands the node a parsed Pydantic object and the ``AIMessage`` that carried the
usage never reaches it; ``on_llm_end`` still sees the raw generation.

What a response's usage means depends on its wire type, so it is read by that
wire's adapter (``nl2sql.llm.wires``), picked by the ``model_provider`` the
client stamps on the message. Every adapter fills the same fields:
``input_tokens``, ``cached_input_tokens``, ``cache_write_input_tokens``,
``output_tokens``, ``reasoning_tokens`` and ``total_tokens``.

A detail the provider did not report is recorded as ``0``. A call whose result
carries no usage at all is recorded with zero tokens and ``usage_reported=False``
so a zero is never mistaken for a measurement. Cached and cache-write tokens are
a subset of ``input_tokens``; reasoning tokens are a subset of ``output_tokens``.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Mapping, Optional
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult
from pydantic import BaseModel, Field

from nl2sql.common.context import current_datasource_id
from nl2sql.common.metrics import token_usage_counter
from nl2sql.llm.wires import wire_named

# Per-model prices, per million tokens: {"input": .., "output": .., "cached_input": ..}.
Prices = Mapping[str, Mapping[str, float]]

_TOKEN_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "output_tokens",
    "reasoning_tokens",
    "total_tokens",
)


class UsageTotals(BaseModel):
    """Token counts, LLM calls and LLM seconds for one node or one question.

    ``latency_s`` is time spent waiting on the model, summed over calls; a
    node's wall-clock time (which also covers its non-LLM work) is in
    ``QueryResult.timings``. ``cost`` is null unless every call counted here
    ran on a model with a configured price.
    """

    calls: int = 0
    input_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0
    latency_s: float = 0.0
    cost: Optional[float] = None


class LLMCallUsage(BaseModel):
    """One model call: which node made it, on which model, and what it used."""

    node: str
    model: str = ""
    input_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0
    latency_s: float = 0.0
    cost: Optional[float] = None
    usage_reported: bool = True
    error: Optional[str] = None


class QuestionUsage(BaseModel):
    """What one question cost: totals, per-node roll-ups and every call.

    ``plan_cache_hits`` counts the sub-queries whose plan came from the plan
    cache; each one made no planner call, so it adds no tokens here.
    """

    total: UsageTotals = Field(default_factory=UsageTotals)
    nodes: Dict[str, UsageTotals] = Field(default_factory=dict)
    calls: List[LLMCallUsage] = Field(default_factory=list)
    plan_cache_hits: int = 0


def _provider(response: LLMResult) -> Optional[str]:
    """The wire that served the call: the ``model_provider`` its client stamped on the message."""
    for generations in response.generations or []:
        for generation in generations:
            meta = getattr(getattr(generation, "message", None), "response_metadata", None) or {}
            if meta.get("model_provider"):
                return str(meta["model_provider"])
    return None


def read_usage(response: LLMResult) -> Optional[Dict[str, int]]:
    """Token counts from a chat model result, or None if it reports no usage.

    Each wire type's adapter knows its own usage shape; the call is read by the
    adapter of the provider that served it (OpenAI's when none is stamped).
    """
    return wire_named(_provider(response)).read_usage(response)


def _model_name(response: LLMResult) -> str:
    for generations in response.generations or []:
        for generation in generations:
            meta = getattr(getattr(generation, "message", None), "response_metadata", None) or {}
            name = meta.get("model_name") or meta.get("model")
            if name:
                return str(name)
    return str((response.llm_output or {}).get("model_name") or "")


def _cost(call: LLMCallUsage, requested_model: str, prices: Optional[Prices]) -> Optional[float]:
    """Price a call by its served model name, else by the configured one.

    No prefix matching: ``gpt-4o`` must not price ``gpt-4o-mini``.
    """
    if not prices:
        return None
    price = prices.get(call.model) or prices.get(requested_model)
    if not price or "input" not in price or "output" not in price:
        return None
    cached_price = price.get("cached_input", price["input"])
    uncached = call.input_tokens - call.cached_input_tokens
    return round(
        (uncached * price["input"] + call.cached_input_tokens * cached_price + call.output_tokens * price["output"])
        / 1_000_000,
        8,
    )


def _roll_up(calls: List[LLMCallUsage]) -> UsageTotals:
    totals = UsageTotals(calls=len(calls))
    for call in calls:
        for name in _TOKEN_FIELDS:
            setattr(totals, name, getattr(totals, name) + getattr(call, name))
        totals.latency_s += call.latency_s
    totals.latency_s = round(totals.latency_s, 4)
    if calls and all(call.cost is not None for call in calls):
        totals.cost = round(sum(call.cost for call in calls), 8)
    return totals


class TokenUsageCallback(BaseCallbackHandler):
    """Records every LLM call's node, model, tokens and latency.

    The node comes from ``metadata["langgraph_node"]``, which LangGraph puts on
    every run inside a node, including the chat-model run. That metadata is
    only passed to the *start* callbacks, so the call is opened there and
    closed in ``on_llm_end``. Sub-queries run in parallel, hence the lock.
    """

    def __init__(self, prices: Optional[Prices] = None) -> None:
        self._prices = prices
        self._lock = threading.Lock()
        self._open: Dict[UUID, tuple] = {}
        self._calls: List[LLMCallUsage] = []
        self._by_run: Dict[UUID, LLMCallUsage] = {}

    def _start(self, run_id: UUID, metadata: Optional[Dict[str, Any]], kwargs: Dict[str, Any]) -> None:
        metadata = metadata or {}
        params = kwargs.get("invocation_params") or {}
        requested = metadata.get("ls_model_name") or params.get("model") or params.get("model_name") or ""
        with self._lock:
            self._open[run_id] = (metadata.get("langgraph_node") or "unknown", str(requested), time.perf_counter())

    def on_chat_model_start(self, serialized, messages, *, run_id: UUID, metadata=None, **kwargs: Any) -> None:
        self._start(run_id, metadata, kwargs)

    def on_llm_start(self, serialized, prompts, *, run_id: UUID, metadata=None, **kwargs: Any) -> None:
        self._start(run_id, metadata, kwargs)

    def _finish(self, run_id: UUID, response: Optional[LLMResult], error: Optional[BaseException]) -> None:
        with self._lock:
            opened = self._open.pop(run_id, None)
        node, requested, t0 = opened or ("unknown", "", time.perf_counter())
        tokens = read_usage(response) if response is not None else None
        call = LLMCallUsage(
            node=node,
            model=(_model_name(response) if response is not None else "") or requested,
            latency_s=round(time.perf_counter() - t0, 4),
            usage_reported=tokens is not None,
            error=str(error) if error is not None else None,
            **(tokens or {}),
        )
        call.cost = _cost(call, requested, self._prices)
        with self._lock:
            self._calls.append(call)
            self._by_run[run_id] = call
        if tokens is not None:
            self._emit_metrics(call)

    def on_llm_end(self, response: LLMResult, *, run_id: UUID, **kwargs: Any) -> None:
        self._finish(run_id, response, None)

    def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        self._finish(run_id, None, error)

    @staticmethod
    def _emit_metrics(call: LLMCallUsage) -> None:
        """The ``nl2sql.token.usage`` OpenTelemetry counter, one point per token type."""
        base = {
            "node": call.node,
            "model": call.model or "unknown",
            "datasource_id": str(current_datasource_id.get() or "none"),
        }
        for kind, value in (
            ("input", call.input_tokens),
            ("cached_input", call.cached_input_tokens),
            ("cache_write_input", call.cache_write_input_tokens),
            ("output", call.output_tokens),
            ("reasoning", call.reasoning_tokens),
            ("total", call.total_tokens),
        ):
            token_usage_counter.add(value, attributes={**base, "type": kind})

    def call_for(self, run_id: UUID) -> Optional[LLMCallUsage]:
        """The record for one finished LLM run, e.g. for a run trace to attach."""
        with self._lock:
            return self._by_run.get(run_id)

    def usage(self) -> QuestionUsage:
        """A snapshot of everything recorded so far."""
        with self._lock:
            calls = list(self._calls)
        by_node: Dict[str, List[LLMCallUsage]] = {}
        for call in calls:
            by_node.setdefault(call.node, []).append(call)
        return QuestionUsage(
            total=_roll_up(calls),
            nodes={node: _roll_up(node_calls) for node, node_calls in by_node.items()},
            calls=calls,
        )
