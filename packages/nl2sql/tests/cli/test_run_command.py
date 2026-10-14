"""``nl2sql run`` shows the run from ``engine.stream_query``'s events, through the public API only.

It exits 1 when the run cannot finish (an ``error`` event) or finishes with a
blocking error (an ERROR or CRITICAL error makes the status "error"); warnings
alone exit 0. ``--show-perf`` is each step's time from its ``node_finished``
event.
"""
from __future__ import annotations

import pytest

from nl2sql import QueryEvent, QueryResult
from nl2sql.cli.commands import run as run_module
from nl2sql.cli.types import RunConfig
from nl2sql.testing.config import make_config


class StreamEngine:
    """Yields the events it is given, and records how it was asked."""

    def __init__(self, config, events):
        self.config = config
        self.events = events
        self.asked = None

    def stream_query(self, question, **kwargs):
        self.asked = (question, kwargs)
        yield from self.events


def _steps():
    return [QueryEvent(type="node_started", data={"node": "decomposer"}),
            QueryEvent(type="node_finished", data={"node": "decomposer", "seconds": 0.25, "ok": True})]


def _final(**fields) -> QueryEvent:
    return QueryEvent(type="final", data=QueryResult(**fields).model_dump(mode="json"))


def _error(severity: str) -> dict:
    return {"node": "decomposer", "message": "Decomposition failed: boom", "severity": severity,
            "error_code": "ORCHESTRATOR_CRASH"}


@pytest.fixture(autouse=True)
def wide_console(monkeypatch):
    monkeypatch.setenv("COLUMNS", "200")


@pytest.fixture
def engine_with(tmp_path):
    return lambda *events: StreamEngine(make_config(tmp_path, settings={"tenant_id": "acme"}), list(events))


def test_run_asks_the_engine_with_the_role_and_the_options(engine_with):
    engine = engine_with(*_steps(), _final(status="success"))

    run_module.run_pipeline(RunConfig(query="How many customers?", ds_id="shop", role="analyst", no_exec=True),
                            engine)

    question, options = engine.asked
    assert question == "How many customers?"
    assert options["datasource_id"] == "shop" and options["execute"] is False
    assert options["user_context"].roles == ["analyst"] and options["user_context"].tenant_id == "acme"


def test_a_run_that_cannot_finish_prints_the_error_and_exits_1(engine_with, capsys):
    engine = engine_with(QueryEvent(type="error", data={"message": "The run failed: no key"}))

    with pytest.raises(SystemExit) as exc:
        run_module.run_pipeline(RunConfig(query="How many customers?"), engine)

    assert exc.value.code == 1
    output = capsys.readouterr().out
    assert "The run failed: no key" in output and "Traceback" not in output


@pytest.mark.parametrize("severity", ["ERROR", "CRITICAL"])
def test_a_blocking_error_exits_1(engine_with, capsys, severity):
    engine = engine_with(*_steps(), _final(status="error", errors=[_error(severity)]))

    with pytest.raises(SystemExit) as exc:
        run_module.run_pipeline(RunConfig(query="How many customers?"), engine)

    assert exc.value.code == 1
    assert "Pipeline Errors" in capsys.readouterr().out


def test_warnings_alone_exit_0(engine_with, capsys):
    engine = engine_with(*_steps(), _final(status="success", warnings=[_error("WARNING")]))

    run_module.run_pipeline(RunConfig(query="How many customers?"), engine)

    assert "Decomposition failed" in capsys.readouterr().out


def test_show_perf_prints_each_steps_time(engine_with, capsys):
    engine = engine_with(*_steps(), _final(status="success"))

    run_module.run_pipeline(RunConfig(query="How many customers?", show_perf=True), engine)

    output = capsys.readouterr().out
    assert "decomposer" in output and "0.25s" in output
