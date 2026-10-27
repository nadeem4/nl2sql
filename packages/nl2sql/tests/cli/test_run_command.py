"""``nl2sql run`` shows the run from ``engine.stream_query``'s events, through the public API only.

It exits 1 when the run cannot finish (a ``run.failed`` event) or finishes with
a blocking error (an ERROR or CRITICAL error makes the status "error");
warnings alone exit 0. ``--show-perf`` is each step's time from its
``node.finished`` event.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from nl2sql import Event, QueryResult
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


def _event(name, **data) -> Event:
    return Event(name=name, at=datetime.now(timezone.utc), trace_id="t-1", data=data)


def _steps():
    return [_event("run.started", question="How many customers?"),
            _event("node.started", node="decomposer", sub_query_id=None),
            _event("node.finished", node="decomposer", sub_query_id=None, seconds=0.25, ok=True)]


def _final(**fields) -> Event:
    return _event("run.finished", status=fields.get("status"), result=QueryResult(**fields).model_dump(mode="json"))


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
    engine = engine_with(_event("run.failed", message="The run failed: no key", error_code="UNKNOWN_ERROR"))

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
    engine = engine_with(*_steps(), _final(status="success", warnings=[{"code": "ORCHESTRATOR_CRASH", "node": "decomposer",
                                                                       "message": "Decomposition failed: boom"}]))

    run_module.run_pipeline(RunConfig(query="How many customers?"), engine)

    assert "Decomposition failed" in capsys.readouterr().out


def test_show_perf_prints_each_steps_time(engine_with, capsys):
    engine = engine_with(*_steps(), _final(status="success"))

    run_module.run_pipeline(RunConfig(query="How many customers?", show_perf=True), engine)

    output = capsys.readouterr().out
    assert "decomposer" in output and "0.25s" in output


# ---------------------------------------------------------------- clarification (L2)


class AskingEngine(StreamEngine):
    """Asks which reading on the first run, answers on the second."""

    def __init__(self, config, first, second=None):
        super().__init__(config, first)
        self.second, self.runs = second, []

    def stream_query(self, question, **kwargs):
        self.runs.append(kwargs)
        yield from (self.events if len(self.runs) == 1 else self.second)


def _clarification():
    from nl2sql import ClarificationOption, ClarificationRequest
    from nl2sql.explain.render import explain_plan
    from nl2sql.pipeline.nodes.ast_planner.schemas import PlanModel

    plan = PlanModel.model_validate({"tables": [{"name": "invoice", "alias": "i"}], "select_items": [
        {"alias": "n", "expr": {"kind": "column", "alias": "i", "column_name": "id"}}]})
    options = [ClarificationOption(id=i, label=label, plan=plan.model_dump(mode="json"),
                                   explanation=explain_plan(plan, datasource_id="ds1"))
               for i, label in (("a", "Gross"), ("b", "Invoiced amount"))]
    return ClarificationRequest.build(
        question="What were sales?", phrase="sales", datasource_id="ds1", schema_version="v1",
        knowledge_digest="d", sub_query={"id": "sq1", "datasource_id": "ds1"}, options=options)


def _asks() -> Event:
    request = _clarification()
    return _final(status="needs_clarification", clarification=request.model_dump(mode="json"),
                  final_answer={"summary": "s", "format_type": "text", "content": "Choose one", "warnings": []})


def test_run_command_asks_on_a_terminal_and_exits_4_otherwise(engine_with, capsys, monkeypatch):
    monkeypatch.setattr(run_module, "_is_terminal", lambda: False)
    engine = engine_with(*_steps(), _asks())

    with pytest.raises(SystemExit) as exc:
        run_module.run_pipeline(RunConfig(query="What were sales?"), engine)

    assert exc.value.code == 4
    output = capsys.readouterr().out
    assert "sales" in output and "1. Gross" in output and "2. Invoiced amount" in output
    assert len(engine.asked) == 2  # asked once: nothing to pick from without a terminal


def test_on_a_terminal_the_pick_runs_the_question_again_with_the_choice(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(run_module, "_is_terminal", lambda: True)
    monkeypatch.setattr(run_module.typer, "prompt", lambda *args, **kwargs: 2)
    engine = AskingEngine(make_config(tmp_path, settings={"tenant_id": "acme"}), [*_steps(), _asks()],
                          [*_steps(), _final(status="success")])

    run_module.run_pipeline(RunConfig(query="What were sales?"), engine)

    first, second = engine.runs
    assert first.get("choice") is None
    assert second["choice"].option_id == "b" and second["choice"].request.id == _clarification().id
    assert second["user_context"] == first["user_context"]


def test_a_re_ask_resets_the_step_timings(tmp_path, monkeypatch):
    from nl2sql.cli.reporting import ConsolePresenter

    monkeypatch.setattr(run_module, "_is_terminal", lambda: True)
    monkeypatch.setattr(run_module.typer, "prompt", lambda *args, **kwargs: 2)
    shown = []
    monkeypatch.setattr(ConsolePresenter, "print_step_timings", lambda self, steps, tokens: shown.append(list(steps)))
    engine = AskingEngine(make_config(tmp_path, settings={"tenant_id": "acme"}), [*_steps(), _asks()],
                          [*_steps(), _final(status="success")])

    run_module.run_pipeline(RunConfig(query="What were sales?", show_perf=True), engine)

    # The decomposer ran in both runs; the table is the last run's steps, not both.
    [steps] = shown
    assert [name for name, _, _ in steps] == ["decomposer"]
