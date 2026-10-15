"""The ``otel`` observer: OpenTelemetry metrics and spans from the event bus (D18).

It needs the ``otel`` extra (``pip install "nl2sql-engine[otel]"``). This is
the only module that imports ``opentelemetry``, and only when the observer is
built, so the registry can import it to read ``requires`` and the install
hint before the extra is known to be installed.

- Metrics: ``nl2sql.node.duration`` (from ``node.finished``),
  ``nl2sql.token.usage`` (from ``llm.call``, one point per token type),
  ``nl2sql.plan_cache.lookups`` (from ``plan_cache.lookup``) and
  ``nl2sql.run.cost`` (from ``run.finished``, when the run was priced).
- Traces: one span per run, ``run.started`` to ``run.finished`` or
  ``run.failed``, with a child span per node, keyed by ``trace_id`` and
  ``sub_query_id``.

The observer makes its own ``MeterProvider`` and ``TracerProvider`` for its
exporter and never sets the process-global ones, so two engines can export to
two collectors. Recording is in memory; the SDK exports in its own background
thread, so ``on_event`` never blocks. See docs/configuration/observers.md.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, ClassVar, Dict, FrozenSet, List, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict

from nl2sql_sdk.events import Event

_TOKEN_TYPES = (("input", "input_tokens"), ("cached_input", "cached_input_tokens"),
                ("cache_write_input", "cache_write_input_tokens"), ("output", "output_tokens"),
                ("reasoning", "reasoning_tokens"), ("total", "total_tokens"))


class OtelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    endpoint: str = "http://localhost:4317"
    exporter: Literal["otlp", "console"] = "otlp"
    metrics: bool = True
    traces: bool = True
    service_name: str = "nl2sql"


def _ns(event: Event) -> int:
    return int(event.at.timestamp() * 1_000_000_000)


class OtelObserver:
    """Records the engine's events as OpenTelemetry metrics and spans."""

    interface_version: ClassVar[int] = 1
    config_model: ClassVar[type[BaseModel]] = OtelConfig
    events: ClassVar[Optional[FrozenSet[str]]] = frozenset(
        {"run.started", "run.finished", "run.failed", "node.started", "node.finished", "llm.call",
         "plan_cache.lookup"})
    # What the registry checks before importing the observer's dependencies.
    requires: ClassVar[Tuple[str, ...]] = ("opentelemetry.sdk", "opentelemetry.exporter.otlp.proto.grpc")
    install_hint: ClassVar[str] = 'pip install "nl2sql-engine[otel]"'

    def __init__(self, config: OtelConfig, *, span_exporter: Any = None, metric_reader: Any = None) -> None:
        """``span_exporter`` and ``metric_reader`` replace the configured exporter (a test's in-memory one)."""
        from opentelemetry.sdk.resources import Resource

        self._lock = threading.Lock()
        self._runs: Dict[str, Any] = {}
        self._nodes: Dict[str, Dict[Tuple[Optional[str], str], List[Any]]] = {}
        resource = Resource.create({"service.name": config.service_name})
        self._tracer_provider = self._tracer = None
        self._meter_provider = None
        if config.traces:
            self._start_traces(config, resource, span_exporter)
        if config.metrics:
            self._start_metrics(config, resource, metric_reader)

    def _start_traces(self, config: OtelConfig, resource: Any, exporter: Any) -> None:
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter, SimpleSpanProcessor

        provider = TracerProvider(resource=resource)
        if exporter is not None:
            provider.add_span_processor(SimpleSpanProcessor(exporter))
        else:
            if config.exporter == "console":
                exporter = ConsoleSpanExporter()
            else:
                from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

                exporter = OTLPSpanExporter(endpoint=config.endpoint)
            provider.add_span_processor(BatchSpanProcessor(exporter))
        self._tracer_provider = provider
        self._tracer = provider.get_tracer("nl2sql")

    def _start_metrics(self, config: OtelConfig, resource: Any, reader: Any) -> None:
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import ConsoleMetricExporter, PeriodicExportingMetricReader

        if reader is None:
            if config.exporter == "console":
                exporter = ConsoleMetricExporter()
            else:
                from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter

                exporter = OTLPMetricExporter(endpoint=config.endpoint)
            reader = PeriodicExportingMetricReader(exporter)
        self._meter_provider = MeterProvider(resource=resource, metric_readers=[reader])
        meter = self._meter_provider.get_meter("nl2sql")
        self._node_duration = meter.create_histogram(
            "nl2sql.node.duration", unit="s", description="Duration of a pipeline step, by node")
        self._token_usage = meter.create_counter(
            "nl2sql.token.usage", unit="1",
            description="LLM tokens, by node, model and type (input, cached_input, cache_write_input, "
                        "output, reasoning, total)")
        self._plan_cache = meter.create_counter(
            "nl2sql.plan_cache.lookups", unit="1", description="Plan cache lookups, by result and datasource_id")
        self._run_cost = meter.create_histogram(
            "nl2sql.run.cost", unit="USD", description="What a priced question cost")

    @classmethod
    def from_config(cls, cfg: OtelConfig, *, folder: Optional[Path]) -> "OtelObserver":
        return cls(cfg)

    # ------------------------------------------------------------ events

    def on_event(self, event: Event) -> None:
        if self._meter_provider is not None:
            self._record(event)
        if self._tracer is not None and event.trace_id:
            self._trace(event)

    def _record(self, event: Event) -> None:
        data = event.data
        if event.name == "node.finished":
            self._node_duration.record(data["seconds"], attributes={"node": data["node"]})
        elif event.name == "llm.call":
            base = {"node": data.get("node") or "unknown", "model": data.get("model") or "unknown"}
            for kind, field in _TOKEN_TYPES:
                self._token_usage.add(data.get(field, 0), attributes={**base, "type": kind})
        elif event.name == "plan_cache.lookup":
            self._plan_cache.add(1, attributes={"result": "hit" if data["hit"] else "miss",
                                                "datasource_id": data["datasource_id"]})
        elif event.name == "run.finished":
            cost = (data.get("usage") or {}).get("cost")
            if cost is not None:
                self._run_cost.record(cost, attributes={"status": str(data.get("status"))})

    def _trace(self, event: Event) -> None:
        from opentelemetry import trace
        from opentelemetry.trace import Status, StatusCode

        data, trace_id = event.data, event.trace_id
        with self._lock:
            if event.name == "run.started":
                self._runs[trace_id] = self._tracer.start_span(
                    "nl2sql.run", start_time=_ns(event),
                    attributes={"nl2sql.trace_id": trace_id, "nl2sql.execute": bool(data.get("execute"))})
            elif event.name == "node.started":
                run = self._runs.get(trace_id)
                parent = trace.set_span_in_context(run) if run is not None else None
                span = self._tracer.start_span(
                    f"nl2sql.node.{data['node']}", context=parent, start_time=_ns(event),
                    attributes={"nl2sql.node": data["node"], "nl2sql.sub_query_id": data.get("sub_query_id") or ""})
                key = (data.get("sub_query_id"), data["node"])
                self._nodes.setdefault(trace_id, {}).setdefault(key, []).append(span)
            elif event.name == "node.finished":
                open_spans = self._nodes.get(trace_id, {}).get((data.get("sub_query_id"), data["node"]))
                if open_spans:
                    span = open_spans.pop()
                    if not data.get("ok", True):
                        span.set_status(Status(StatusCode.ERROR))
                    span.end(end_time=_ns(event))
            elif event.name in ("run.finished", "run.failed"):
                for spans in self._nodes.pop(trace_id, {}).values():
                    for span in spans:
                        span.end(end_time=_ns(event))
                run = self._runs.pop(trace_id, None)
                if run is None:
                    return
                if event.name == "run.finished":
                    run.set_attributes({"nl2sql.status": str(data.get("status")),
                                        "nl2sql.source": str(data.get("source")),
                                        "nl2sql.datasources": list(data.get("datasources") or [])})
                    if data.get("status") == "error":
                        run.set_status(Status(StatusCode.ERROR))
                else:
                    run.set_attribute("nl2sql.error_code", str(data.get("error_code")))
                    run.set_status(Status(StatusCode.ERROR, str(data.get("message"))))
                run.end(end_time=_ns(event))

    def close(self) -> None:
        """Flush and shut down the observer's own providers."""
        for provider in (self._tracer_provider, self._meter_provider):
            if provider is not None:
                provider.shutdown()
