"""Observers: the ``observers`` section built, and the built-in ``log``, ``audit`` and ``otel``.

docs/architecture/wave_a.md, section 4, row E2. Observers fail open and never
block (D9); with no section the engine runs ``log`` and ``audit`` (D18); the
audit log is JSON lines under ``storage.path/audit/``, one summary line per
run, purged by ``engine.storage.purge()`` (D19).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from langchain_core.embeddings import DeterministicFakeEmbedding
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from nl2sql import LLM, NL2SQL, ConfigError, Embeddings, ObserverConfig, Registry
from nl2sql.events import EventBus, RunScope
from nl2sql.events.catalog import CATALOG, EventSpec, HookDecision
from nl2sql.observers.audit import AuditConfig, AuditObserver
from nl2sql.observers.log import LogConfig, LogObserver
from nl2sql.policies import UserContext
from nl2sql.testing.config import make_config

SECRET = "sk-test-observers-do-not-print"
_USER = UserContext(user_id="ada", tenant_id="acme", roles=["analyst"])


class _Client(FakeListChatModel):
    model_name: str = ""


class _Provider:
    interface_version = 2
    label = "Fake"
    api_key_env = None
    requires = ()
    install_hint = ""
    structured_output = "tool_calling"

    def create(self, agent):
        return _Client(responses=["ok"], model_name=agent.model)


@pytest.fixture
def make_engine(tmp_path: Path):
    engines = []

    def build(**sections) -> NL2SQL:
        registry = Registry.default()
        registry.add_llm("fake", _Provider())
        config = make_config(tmp_path, llm={"provider": "fake", "model": "m1", "api_key": SECRET}, **sections)
        config = config.model_copy(update={"embeddings": Embeddings(instance=DeterministicFakeEmbedding(size=8))})
        engine = NL2SQL(config, registry=registry)
        engines.append(engine)
        return engine

    yield build
    for engine in engines:
        engine.close()


def _utc_today():
    """The audit log names its files by the UTC date, not the local one."""
    return datetime.now(timezone.utc).date()


def _audit_lines(folder: Path):
    return [json.loads(line) for path in sorted((folder / "audit").glob("*.jsonl"))
            for line in path.read_text(encoding="utf-8").splitlines()]


def _run(bus: EventBus, *, hooks=()):
    """One run's events on ``bus``: started, a step, hook decisions, finished."""
    scope = RunScope(bus, question="How many?", user_context=_USER)
    scope.start()
    scope.publish("node.started", {"node": "planner"})
    scope.publish("node.finished", {"node": "planner", "seconds": 0.1})
    for decision in hooks:
        scope.publish("hook.decision", {"hook": "corp", "point": "execute.before", "decision": decision})
    scope._close("run.finished", {"status": "success", "duration_s": 0.2, "user_id": "ada", "roles": ["analyst"],
                                  "datasources": ["payroll"], "tables": ["payroll.main.salaries"]})
    return scope


# ---------------------------------------------------------------- the section

def test_default_observers_are_log_and_audit(make_engine):
    engine = make_engine()
    assert [type(o) for o in engine._runtime.observers] == [LogObserver, AuditObserver]


def test_an_empty_section_runs_no_observer(make_engine):
    assert make_engine(observers=[])._runtime.observers == []


def test_an_observer_object_is_used_as_given(make_engine):
    seen = []

    class Mine:
        events = None

        def on_event(self, event):
            seen.append(event.name)

    engine = make_engine()
    mine = Mine()
    engine = NL2SQL(engine.config.model_copy(update={"observers": [mine]}), registry=engine._runtime.registry)
    try:
        engine.llm.replace(LLM(provider="fake", model="m2", api_key=SECRET))
    finally:
        engine.close()
    assert engine._runtime.observers == [mine]
    assert seen == ["llm.changed"]


def test_an_unknown_observer_is_a_config_error_with_did_you_mean(make_engine):
    with pytest.raises(ConfigError, match="Did you mean 'audit'"):
        make_engine(observers=[{"type": "audti"}])


def test_an_observers_keys_are_checked_by_its_config_model(make_engine):
    with pytest.raises(ConfigError, match="max_age_days"):
        make_engine(observers=[{"type": "audit", "max_age_days": 0}])


def test_otel_without_the_extra_names_the_fix(make_engine, monkeypatch):
    import nl2sql.registry as registry_module

    real = registry_module._installed
    monkeypatch.setattr(registry_module, "_installed",
                        lambda module: False if module.startswith("opentelemetry") else real(module))
    with pytest.raises(ConfigError, match=r'pip install "nl2sql-engine\[otel\]"'):
        make_engine(observers=[{"type": "otel"}])


def test_observer_exception_is_logged_once_and_skipped(make_engine, caplog):
    class Broken:
        events = None

        def on_event(self, event):
            raise RuntimeError("observer bug")

    engine = make_engine(observers=[])
    engine = NL2SQL(engine.config.model_copy(update={"observers": [Broken()]}),
                    registry=engine._runtime.registry)
    try:
        with caplog.at_level(logging.WARNING, logger="nl2sql.events"):
            engine.llm.replace(LLM(provider="fake", model="m2", api_key=SECRET))
            engine.llm.replace(LLM(provider="fake", model="m3", api_key=SECRET))
    finally:
        engine.close()
    warnings = [r for r in caplog.records if "observer bug" in r.getMessage()]
    assert len(warnings) == 1
    assert engine._runtime.bus.failures == 2


# ---------------------------------------------------------------- log

def test_log_skips_node_started_and_answer_delta_below_debug(caplog):
    bus = EventBus()
    bus.subscribe(LogObserver.from_config(LogConfig(), folder=Path(".")))
    with caplog.at_level(logging.DEBUG, logger="nl2sql.events"):
        _run(bus)
    lines = [r.getMessage() for r in caplog.records if r.name == "nl2sql.events"]
    assert [line.split()[0] for line in lines] == ["run.started", "node.finished", "run.finished"]
    assert all(r.levelno == logging.INFO for r in caplog.records if r.name == "nl2sql.events")


def test_log_at_debug_logs_every_event(caplog):
    bus = EventBus()
    bus.subscribe(LogObserver.from_config(LogConfig(level="DEBUG"), folder=Path(".")))
    with caplog.at_level(logging.DEBUG, logger="nl2sql.events"):
        _run(bus)
    lines = [r.getMessage() for r in caplog.records if r.name == "nl2sql.events"]
    assert "node.started" in [line.split()[0] for line in lines]


def test_log_line_carries_a_short_summary_of_the_data(caplog):
    bus = EventBus()
    bus.subscribe(LogObserver.from_config(LogConfig(events=["run.finished"]), folder=Path(".")))
    with caplog.at_level(logging.INFO, logger="nl2sql.events"):
        _run(bus)
    [line] = [r.getMessage() for r in caplog.records if r.name == "nl2sql.events"]
    assert line.startswith("run.finished ") and "status=success" in line


def test_log_events_must_be_catalogued():
    with pytest.raises(ValueError, match="Not in the event catalog"):
        LogConfig(events=["no.such.event"])


# ---------------------------------------------------------------- audit

def test_audit_writes_under_storage_path(make_engine):
    engine = make_engine()
    engine.llm.replace(LLM(provider="fake", model="m2", api_key=SECRET), by=_USER, via="api")
    engine.close()

    folder = engine.config.storage.path
    [line] = _audit_lines(folder)
    assert line["name"] == "llm.changed"
    assert line["data"]["by"] == {"user_id": "ada", "roles": ["analyst"], "via": "api"}
    assert line["data"]["changes"]["default"] == {"old": "fake/m1", "new": "fake/m2"}
    assert [p.name for p in (folder / "audit").iterdir()] == [f"{_utc_today().isoformat()}.jsonl"]
    assert SECRET not in (folder / "audit" / f"{_utc_today().isoformat()}.jsonl").read_text(encoding="utf-8")


def test_audit_writes_one_summary_per_run(tmp_path):
    bus = EventBus()
    audit = AuditObserver.from_config(AuditConfig(), folder=tmp_path)
    bus.subscribe(audit)
    scope = _run(bus, hooks=["allow", "modify", "deny", "needs_approval", "approved", "error"])
    audit.close()

    lines = _audit_lines(tmp_path)
    finished = [line for line in lines if line["name"] == "run.finished"]
    assert len(finished) == 1
    [summary] = finished
    assert summary["trace_id"] == scope.trace_id and summary["tenant_id"] == "acme"
    assert summary["data"]["user_id"] == "ada" and summary["data"]["tables"] == ["payroll.main.salaries"]
    assert "rows" not in json.dumps(summary)
    assert [line["data"]["decision"] for line in lines if line["name"] == "hook.decision"] == [
        "deny", "needs_approval", "approved", "error"]
    assert {line["name"] for line in lines} == {"run.finished", "hook.decision"}


def test_audit_writes_a_failed_run_with_its_user(tmp_path):
    bus = EventBus()
    audit = AuditObserver.from_config(AuditConfig(), folder=tmp_path)
    bus.subscribe(audit)
    scope = RunScope(bus, question="q", user_context=_USER)
    scope.start()
    scope.fail("boom", "UNKNOWN_ERROR")
    audit.close()

    [line] = _audit_lines(tmp_path)
    assert line["name"] == "run.failed"
    assert line["data"]["user_id"] == "ada" and line["data"]["roles"] == ["analyst"]


def test_the_audited_events_come_from_the_catalog(monkeypatch, tmp_path):
    """A later wave adds an audited event by its catalog entry alone (Wave C: datasource.saved)."""
    monkeypatch.setitem(CATALOG, "eval.finished",
                        CATALOG["eval.finished"].model_copy(update={"audit": True}))
    bus = EventBus()
    audit = AuditObserver.from_config(AuditConfig(), folder=tmp_path)
    bus.subscribe(audit)
    bus.publish("eval.finished", {"suite": "chinook", "cases": 3})
    bus.publish("eval.case", {"suite": "chinook", "case_id": "c1", "role": "analyst"})
    audit.close()

    assert [line["name"] for line in _audit_lines(tmp_path)] == ["eval.finished"]


def test_audit_keeps_only_the_values_the_catalog_names():
    assert CATALOG["hook.decision"].audit_only == {"decision": frozenset(
        {"deny", "needs_approval", "approved", "error"})}
    assert isinstance(CATALOG["hook.decision"], EventSpec)
    assert set(CATALOG["hook.decision"].audit_only["decision"]) < set(
        HookDecision.model_fields["decision"].annotation.__args__)


def test_audit_never_blocks_the_publisher(tmp_path, monkeypatch):
    """The write happens on the observer's own thread: a slow disk never holds a run's lock."""
    import threading

    audit = AuditObserver.from_config(AuditConfig(), folder=tmp_path)
    gate = threading.Event()
    real = audit._write

    def slow(batch):
        gate.wait(5)
        real(batch)

    monkeypatch.setattr(audit, "_write", slow)
    bus = EventBus()
    bus.subscribe(audit)
    finished = threading.Event()

    def publish():
        _run(bus)
        finished.set()

    threading.Thread(target=publish).start()
    assert finished.wait(2), "publishing waited for the audit write"
    gate.set()
    audit.close()
    assert [line["name"] for line in _audit_lines(tmp_path)] == ["run.finished"]


def test_purge_deletes_old_audit_files(make_engine):
    engine = make_engine(observers=[{"type": "audit", "max_age_days": 30}])
    folder = engine.config.storage.path / "audit"
    folder.mkdir(parents=True)
    old = folder / f"{(_utc_today() - timedelta(days=31)).isoformat()}.jsonl"
    kept = folder / f"{(_utc_today() - timedelta(days=29)).isoformat()}.jsonl"
    for path in (old, kept):
        path.write_text("{}\n", encoding="utf-8")

    counts = engine.storage.purge()

    assert counts["audit"] == 1
    assert not old.exists() and kept.exists()


def test_purge_without_an_audit_observer_leaves_audit_out(make_engine):
    assert "audit" not in make_engine(observers=[]).storage.purge()


# ---------------------------------------------------------------- llm.changed

def test_llm_replace_publishes_llm_changed_without_keys(make_engine):
    engine = make_engine(observers=[])
    seen = []
    engine.events.subscribe(seen.append, names=["llm.changed"])
    engine.llm.replace(LLM(provider="fake", model="m2", api_key=SECRET + "-new"), by=_USER, via="api")

    [event] = seen
    assert event.trace_id is None and event.tenant_id == engine._runtime.tenant_id
    assert event.data["by"] == {"user_id": "ada", "roles": ["analyst"], "via": "api"}
    assert event.data["changes"]["default"] == {"old": "fake/m1", "new": "fake/m2"}
    text = event.model_dump_json()
    assert SECRET not in text and "api_key" not in text


def test_the_old_audit_and_metrics_modules_are_gone():
    import importlib

    for name in ("nl2sql.common.event_logger", "nl2sql.common.metrics"):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(name)


# ---------------------------------------------------------------- plan_cache.lookup

def test_plan_cache_lookup_is_published_in_the_run():
    from types import SimpleNamespace

    from nl2sql.pipeline.plan_cache import PlanCache

    class Store:
        def get_plan(self, *key):
            return None

    bus = EventBus()
    seen = []
    bus.subscribe(seen.append, names=["plan_cache.lookup"])
    scope = RunScope(bus, question="q")
    sub_query = SimpleNamespace(intent="How many?", datasource_id="shop", schema_version="v1")
    with scope.entered():
        PlanCache(Store()).get(sub_query)
    assert [(e.data["datasource_id"], e.data["hit"], e.trace_id) for e in seen] == [
        ("shop", False, scope.trace_id)]


def test_observer_config_is_exported():
    assert ObserverConfig(type="log").type == "log"


# ---------------------------------------------------------------- audit: durability and bounds

def test_audit_files_are_named_by_the_utc_date(tmp_path):
    from nl2sql_sdk.events import Event

    audit = AuditObserver(tmp_path)
    late_evening = datetime.fromisoformat("2026-01-01T23:30:00-05:00")
    audit.on_event(Event(name="llm.changed", at=late_evening, data={"by": {}, "changes": {}}))
    audit.close()
    assert [p.name for p in tmp_path.iterdir()] == ["2026-01-02.jsonl"]


def test_audit_lines_are_written_when_the_process_exits_without_close(tmp_path):
    """A script or notebook that never closes its engine still keeps its audit trail."""
    import subprocess
    import sys

    code = ("from nl2sql.events import EventBus; from nl2sql.observers.audit import AuditObserver; "
            f"bus = EventBus(); bus.subscribe(AuditObserver({str(tmp_path)!r})); "
            "[bus.publish('llm.changed', {'by': {'via': 'python'}, 'changes': {}}) for _ in range(50)]")
    subprocess.run([sys.executable, "-c", code], check=True, timeout=60)
    lines = [line for path in tmp_path.glob("*.jsonl") for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 50


def test_close_unregisters_the_exit_hook(tmp_path, monkeypatch):
    import atexit

    registered, unregistered = [], []
    monkeypatch.setattr(atexit, "register", lambda fn, *a, **k: registered.append(fn) or fn)
    monkeypatch.setattr(atexit, "unregister", lambda fn: unregistered.append(fn))
    audit = AuditObserver(tmp_path)
    audit.on_event(_changed())
    audit.close()
    assert registered and unregistered == registered


def _changed():
    from nl2sql_sdk.events import Event

    return Event(name="llm.changed", at=datetime.now(timezone.utc), data={"by": {}, "changes": {}})


def test_an_event_queued_while_closing_is_still_written(tmp_path):
    """close() writes what is left after its writer stops, and what came before a writer started."""
    audit = AuditObserver(tmp_path)
    audit._queue.put((_utc_today().isoformat(), _changed().model_dump_json()))  # queued, no writer yet
    audit.close()
    lines = [line for path in tmp_path.glob("*.jsonl") for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 1


def test_a_full_audit_queue_drops_and_warns_once(tmp_path, monkeypatch, caplog):
    import threading

    audit = AuditObserver(tmp_path, max_queue=2)
    gate = threading.Event()
    real = audit._write
    monkeypatch.setattr(audit, "_write", lambda batch: (gate.wait(5), real(batch)))
    with caplog.at_level(logging.WARNING, logger="nl2sql.events"):
        for _ in range(10):
            audit.on_event(_changed())  # never blocks, even with the writer stuck
    assert audit.dropped >= 7
    assert len([r for r in caplog.records if "dropped" in r.getMessage()]) == 1
    gate.set()
    audit.close()


def test_run_failed_redacts_a_key_in_the_message(tmp_path):
    """D15: a provider error that quotes a key never reaches the bus, the stream or the audit log."""
    bus = EventBus()
    audit = AuditObserver(tmp_path)
    bus.subscribe(audit)
    scope = RunScope(bus, question="q", user_context=_USER)
    scope.start()
    event = scope.fail("The run failed: invalid key sk-abcdefghijklmnopqrstuvwxyz0123")
    audit.close()

    assert "sk-abcdefghijklmnopqrstuvwxyz0123" not in event.data["message"]
    assert "[redacted key]" in event.data["message"]
    text = "".join(p.read_text(encoding="utf-8") for p in tmp_path.glob("*.jsonl"))
    assert "[redacted key]" in text and "sk-abcdefghijklmnopqrstuvwxyz0123" not in text
