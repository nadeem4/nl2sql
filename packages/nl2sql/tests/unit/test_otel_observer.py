"""The ``otel`` observer: metrics and spans from the bus, on providers of its own (wave_a.md, section 4, E2).

It needs the ``otel`` extra; CI installs it. The exporters here are the SDK's
in-memory ones, so nothing leaves the process.
"""
from __future__ import annotations

import pytest

pytest.importorskip("opentelemetry.sdk")

from opentelemetry.sdk.metrics.export import InMemoryMetricReader  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter  # noqa: E402

from nl2sql.events import EventBus, RunScope  # noqa: E402
from nl2sql.observers.otel import OtelConfig, OtelObserver  # noqa: E402
from nl2sql.policies import UserContext  # noqa: E402


def _observer(**config):
    spans, reader = InMemorySpanExporter(), InMemoryMetricReader()
    observer = OtelObserver(OtelConfig(**config), span_exporter=spans, metric_reader=reader)
    return observer, spans, reader


def _metrics(reader):
    """{metric name: [(attributes, value)]} from the reader's current collection."""
    found = {}
    data = reader.get_metrics_data()
    for resource in data.resource_metrics if data else []:
        for scope in resource.scope_metrics:
            for metric in scope.metrics:
                for point in metric.data.data_points:
                    value = getattr(point, "value", None)
                    if value is None:
                        value = point.sum
                    found.setdefault(metric.name, []).append((dict(point.attributes), value))
    return found


def _run(bus):
    scope = RunScope(bus, question="How many?", user_context=UserContext(user_id="ada", roles=["analyst"]))
    scope.start()
    scope.publish("node.started", {"node": "planner", "sub_query_id": "sq1"})
    scope.publish("llm.call", {"node": "planner", "model": "gpt-test", "input_tokens": 10, "output_tokens": 2,
                               "total_tokens": 12})
    scope.publish("plan_cache.lookup", {"datasource_id": "shop", "hit": False})
    scope.publish("node.finished", {"node": "planner", "sub_query_id": "sq1", "seconds": 0.25})
    scope._close("run.finished", {"status": "success", "duration_s": 0.5,
                                  "usage": {"calls": 1, "cost": 0.01}})
    return scope


def test_otel_exports_node_spans_and_metrics():
    observer, spans, reader = _observer()
    bus = EventBus()
    bus.subscribe(observer)
    scope = _run(bus)

    finished = {span.name: span for span in spans.get_finished_spans()}
    assert set(finished) == {"nl2sql.run", "nl2sql.node.planner"}
    run, node = finished["nl2sql.run"], finished["nl2sql.node.planner"]
    assert node.parent.span_id == run.context.span_id
    assert run.attributes["nl2sql.trace_id"] == scope.trace_id
    assert run.attributes["nl2sql.status"] == "success"
    assert node.attributes["nl2sql.sub_query_id"] == "sq1"

    metrics = _metrics(reader)
    assert metrics["nl2sql.node.duration"] == [({"node": "planner"}, pytest.approx(0.25))]
    tokens = {attrs["type"]: value for attrs, value in metrics["nl2sql.token.usage"]}
    assert tokens["input"] == 10 and tokens["output"] == 2 and tokens["total"] == 12
    assert metrics["nl2sql.plan_cache.lookups"] == [({"result": "miss", "datasource_id": "shop"}, 1)]
    assert "nl2sql.run.cost" in metrics
    observer.close()


def test_a_failed_run_ends_its_span_with_an_error():
    observer, spans, _ = _observer()
    bus = EventBus()
    bus.subscribe(observer)
    scope = RunScope(bus, question="q")
    scope.start()
    scope.publish("node.started", {"node": "planner"})
    scope.fail("boom", "UNKNOWN_ERROR")

    names = sorted(span.name for span in spans.get_finished_spans())
    assert names == ["nl2sql.node.planner", "nl2sql.run"]
    [run] = [span for span in spans.get_finished_spans() if span.name == "nl2sql.run"]
    assert not run.status.is_ok
    observer.close()


def test_metrics_and_traces_can_be_turned_off():
    observer, spans, reader = _observer(traces=False, metrics=False)
    bus = EventBus()
    bus.subscribe(observer)
    _run(bus)
    assert spans.get_finished_spans() == ()
    assert _metrics(reader) == {}
    observer.close()


def test_two_engines_export_to_two_otel_providers():
    first, first_spans, _ = _observer(service_name="one")
    second, second_spans, _ = _observer(service_name="two")
    one, two = EventBus(), EventBus()
    one.subscribe(first)
    two.subscribe(second)
    _run(one)

    assert len(first_spans.get_finished_spans()) == 2
    assert second_spans.get_finished_spans() == ()
    assert first_spans.get_finished_spans()[0].resource.attributes["service.name"] == "one"
    first.close()
    second.close()


def test_from_config_builds_its_own_exporters_without_touching_the_globals():
    from opentelemetry import metrics, trace

    before = (trace.get_tracer_provider(), metrics.get_meter_provider())
    observer = OtelObserver.from_config(OtelConfig(exporter="console"), folder=None)
    try:
        assert (trace.get_tracer_provider(), metrics.get_meter_provider()) == before
    finally:
        observer.close()
