"""The event bus: ``nl2sql_sdk.events.Event`` and ``Observer``, and ``nl2sql.events.EventBus``.

The design is docs/architecture/wave_a.md, section 3: synchronous and in
process, one bus per engine, subscribers fail open unless they ask to raise,
and only catalogued names with valid data are published.
"""
from __future__ import annotations

import ast
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from nl2sql.events import EventBus
from nl2sql.events.catalog import CATALOG, RUN_EVENTS, catalog
from nl2sql_sdk.events import Event, Observer

SDK_EVENTS = Path(__file__).resolve().parents[3] / "sdk" / "src" / "nl2sql_sdk" / "events.py"


# ---------------------------------------------------------------- the SDK's Event and Observer

def test_sdk_events_imports_only_pydantic():
    tree = ast.parse(SDK_EVENTS.read_text(encoding="utf-8"))
    imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    imported |= {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
    outside = {name for name in imported
               if name.partition(".")[0] not in {"pydantic", "__future__"} | set(sys.stdlib_module_names)}
    assert not outside, f"nl2sql_sdk.events may import only pydantic and the standard library: {outside}"


def test_an_event_is_frozen_and_has_its_fields():
    event = Event(name="run.started", at=datetime(2026, 10, 14, tzinfo=timezone.utc), trace_id="t1",
                  tenant_id="acme", data={"question": "How many?"})

    with pytest.raises(ValidationError):
        event.name = "run.failed"
    assert Event(name="index.started", at=event.at).trace_id is None
    assert Event(name="index.started", at=event.at).data == {}


def test_sse_is_one_server_sent_event_holding_the_event_as_json():
    event = Event(name="run.started", at=datetime(2026, 10, 14, tzinfo=timezone.utc), trace_id="t1",
                  data={"question": "How many?"})

    text = event.sse()

    assert text.startswith("event: run.started\ndata: ") and text.endswith("\n\n")
    body = json.loads(text.split("data: ", 1)[1])
    assert body["name"] == "run.started" and body["trace_id"] == "t1"
    assert body["data"] == {"question": "How many?"}


def test_an_observer_class_satisfies_the_protocol():
    class Cfg(BaseModel):
        pass

    class Printer:
        interface_version = 1
        config_model = Cfg
        events = None

        @classmethod
        def from_config(cls, cfg, *, folder):
            return cls()

        def on_event(self, event):
            pass

        def close(self):
            pass

    assert isinstance(Printer(), Observer)


# ---------------------------------------------------------------- the bus

def test_publish_calls_subscribers_in_order():
    bus, seen = EventBus(), []
    bus.subscribe(lambda e: seen.append(("first", e.name)))
    bus.subscribe(lambda e: seen.append(("second", e.name)))

    event = bus.publish("index.started", {"datasource_id": "shop", "namespace": "ns"})

    assert seen == [("first", "index.started"), ("second", "index.started")]
    assert isinstance(event, Event) and event.at.tzinfo is not None


def test_subscribe_filters_by_name_and_close_detaches():
    bus, seen = EventBus(), []
    subscription = bus.subscribe(seen.append, names=["index.failed"])

    bus.publish("index.started", {"datasource_id": "shop", "namespace": "ns"})
    bus.publish("index.failed", {"datasource_id": "shop", "reason": "boom"})
    subscription.close()
    bus.publish("index.failed", {"datasource_id": "shop", "reason": "again"})

    assert [e.data["reason"] for e in seen] == ["boom"]


def test_subscribe_refuses_an_uncatalogued_name():
    with pytest.raises(ValueError, match="no.such"):
        EventBus().subscribe(lambda e: None, names=["no.such"])


def test_a_raising_subscriber_never_fails_the_publisher(caplog):
    bus, seen = EventBus(), []

    def broken(event):
        raise RuntimeError("observer down")

    bus.subscribe(broken)
    bus.subscribe(seen.append)

    with caplog.at_level(logging.WARNING, logger="nl2sql.events"):
        for _ in range(3):
            bus.publish("index.started", {"datasource_id": "shop", "namespace": "ns"})

    assert len(seen) == 3, "a broken subscriber must not stop the next one"
    assert bus.failures == 3
    warnings = [r for r in caplog.records if "observer down" in r.getMessage()]
    assert len(warnings) == 1, "logged once per subscriber and event name"


def test_raise_subscribers_propagate():
    bus = EventBus()

    def collector(event):
        raise RuntimeError("collector bug")

    bus.subscribe(collector, fail="raise")

    with pytest.raises(RuntimeError, match="collector bug"):
        bus.publish("index.started", {"datasource_id": "shop", "namespace": "ns"})


def test_publish_refuses_an_uncatalogued_name():
    with pytest.raises(ValueError, match="not in the event catalog"):
        EventBus().publish("node.exploded", {})


def test_publish_validates_data_against_the_catalog():
    bus = EventBus()

    with pytest.raises(ValidationError):
        bus.publish("index.failed", {"datasource_id": "shop"})  # reason is required
    with pytest.raises(ValidationError):
        bus.publish("index.failed", {"datasource_id": "shop", "reason": "x", "rows": [[1]]})  # strict keys


def test_publish_takes_the_catalog_model_and_dumps_it_as_json():
    bus, seen = EventBus(), []
    bus.subscribe(seen.append)
    model = CATALOG["index.failed"].model(datasource_id="shop", reason="boom")

    bus.publish("index.failed", model, trace_id="t1", tenant_id="acme")

    assert seen[0].data == {"datasource_id": "shop", "reason": "boom"}
    assert (seen[0].trace_id, seen[0].tenant_id) == ("t1", "acme")


def test_close_closes_every_observer_subscribed_and_detaches_it():
    bus, closed, seen = EventBus(), [], []

    class Recorder:
        def on_event(self, event):
            seen.append(event.name)

        def close(self):
            closed.append(True)

    bus.subscribe(Recorder())
    bus.close()
    bus.publish("index.started", {"datasource_id": "shop", "namespace": "ns"})

    assert closed == [True] and seen == []


# ---------------------------------------------------------------- the catalog

WAVE_A_EVENTS = {
    "run.started", "run.finished", "run.failed", "node.started", "node.finished", "answer.delta",
    "result.manifest", "llm.call", "llm.parse_retry", "plan_cache.lookup", "policy.decision",
    "hook.decision", "run.limit_reached", "sanity.flagged",
    "datasource.failed", "index.started", "index.column_failed", "index.swapped", "index.failed",
    "schema.drifted", "retention.purged", "llm.changed", "eval.case", "eval.finished",
}
# docs/architecture/wave_b.md, section 11, added by its foundation PR (B0).
WAVE_B_EVENTS = {
    "knowledge.loaded", "knowledge.used", "clarification.requested", "cost.estimated", "profile.finished",
    "proposal.created", "proposal.decided",
}


def test_the_catalog_lists_every_wave_a_and_wave_b_event():
    assert set(CATALOG) == WAVE_A_EVENTS | WAVE_B_EVENTS
    assert RUN_EVENTS == {"run.started", "run.finished", "run.failed", "node.started", "node.finished",
                          "answer.delta", "result.manifest", "llm.call", "llm.parse_retry",
                          "plan_cache.lookup", "policy.decision", "hook.decision", "run.limit_reached",
                          "sanity.flagged", "knowledge.used", "clarification.requested", "cost.estimated"}


def test_catalog_describes_each_event_with_its_json_schema():
    infos = {info.name: info for info in catalog()}

    assert set(infos) == WAVE_A_EVENTS | WAVE_B_EVENTS
    finished = infos["run.finished"]
    assert finished.description
    assert "status" in finished.data_schema["properties"]


def test_no_catalogued_event_has_a_rows_field():
    def keys(schema):
        if isinstance(schema, dict):
            for key, value in schema.items():
                if key == "properties":
                    yield from value
                yield from keys(value)
        elif isinstance(schema, list):
            for value in schema:
                yield from keys(value)

    for info in catalog():
        assert "rows" not in set(keys(info.data_schema)), info.name
