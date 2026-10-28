"""``nl2sql dashboards refresh`` (docs/architecture/wave_c.md, section 6, row D2).

The command is a thin face on ``engine.dashboards``: ``--dashboard`` refreshes one as the caller ``--as`` builds,
``--due`` calls ``run_scheduled`` so that cron can drive schedules. The charts run through a canned ``run_query``.
"""
from __future__ import annotations

import re

import pytest
from typer.testing import CliRunner

from nl2sql import NL2SQL
from nl2sql.api.dashboards_api import DashboardsAPI
from nl2sql.cli.main import app
from nl2sql.dashboards.models import DashboardRun, TileRun
from nl2sql.testing.config import make_config
from test_dashboards_d2 import NOW, ROLES, Runs, failed, good, seed

runner = CliRunner()
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
SECTIONS = {"policies": {"roles": ROLES}}


@pytest.fixture
def project(tmp_path):
    make_config(tmp_path, **SECTIONS)
    return tmp_path


@pytest.fixture
def runs(monkeypatch):
    return Runs(monkeypatch)


def _run(project, *args):
    result = runner.invoke(app, ["--config", str(project / "nl2sql.yaml"), "dashboards", *args])
    return result, re.sub(r"\s+", " ", ANSI.sub("", result.output))


def _seed(project, **fields):
    with NL2SQL(make_config(project, **SECTIONS)) as engine:
        seed(engine, **fields)


def test_refresh_one_dashboard_runs_its_charts_as_the_caller_and_prints_each(project, runs):
    _seed(project)
    runs.respond = lambda call: (failed("SECURITY_VIOLATION", "logical_validator", "No access to orders.")
                                 if call["question"] == "What is t2?" else good())

    result, out = _run(project, "refresh", "--dashboard", "d1", "--as", "analyst", "--as", "sales", "--user", "ana",
                       "--tenant", "acme")

    assert result.exit_code == 0, out
    assert "d1" in out and "ok 1" in out and "denied 1" in out
    assert "t2" in out and "No access to orders." in out
    assert {(c["user_context"].user_id, tuple(c["user_context"].roles)) for c in runs.calls} == {
        ("ana", ("analyst", "sales"))}


def test_refresh_due_calls_run_scheduled(project, monkeypatch):
    calls = []
    ran = DashboardRun(dashboard_id="d9", at=NOW, trigger="schedule", tiles=[
        TileRun(tile_id="t1", at=NOW, status="ok"), TileRun(tile_id="t2", at=NOW, status="needs_approval")])
    monkeypatch.setattr(DashboardsAPI, "run_scheduled", lambda self, now=None: calls.append(now) or [ran])

    result, out = _run(project, "refresh", "--due")

    assert result.exit_code == 0, out
    assert calls == [None] and "d9" in out and "ok 1" in out and "needs_approval 1" in out


def test_refresh_due_with_nothing_due_says_so(project, monkeypatch):
    monkeypatch.setattr(DashboardsAPI, "run_scheduled", lambda self, now=None: [])

    result, out = _run(project, "refresh", "--due")

    assert result.exit_code == 0 and "No dashboard is due" in out


@pytest.mark.parametrize("args, message", [
    ([], "--dashboard"),
    (["--dashboard", "d1", "--due"], "--dashboard"),
    (["--dashboard", "d1", "--as", "nobody", "--user", "ana"], "Unknown role"),
    (["--dashboard", "d1", "--as", "analyst"], "--dashboard acts as the dashboard's owner: pass --user"),
    (["--dashboard", "missing", "--as", "analyst", "--user", "ana"], "No dashboard 'missing'"),
    (["--due", "--as", "analyst"], "--due runs each schedule as its owner"),
    (["--due", "--user", "ana"], "--due runs each schedule as its owner"),
    (["--due", "--tenant", "acme"], "--due runs each schedule as its owner"),
])
def test_a_bad_call_is_an_error(project, runs, args, message):
    result, out = _run(project, "refresh", *args)

    assert result.exit_code == 1 and message in out


def test_the_help_says_user_and_tenant_act_as_that_owner(project):
    result, out = _run(project, "refresh", "--help")

    assert result.exit_code == 0 and "operator" in out


def test_a_dashboard_being_refreshed_is_an_error(project, runs):
    _seed(project)
    with NL2SQL(make_config(project, **SECTIONS)) as engine:
        with engine._runtime.state.lock("dashboard-refresh:d1", 60):
            result, out = _run(project, "refresh", "--dashboard", "d1", "--as", "analyst", "--user", "ana",
                               "--tenant", "acme")

    assert result.exit_code == 1 and "being refreshed" in out
