"""``nl2sql-api`` starts from ``nl2sql.yaml`` and calls ``engine.check()`` before it serves.

The lifespan loads the ``.env`` next to
``NL2SQL_CONFIG``'s file (never overriding a variable already set), builds the
engine with ``NL2SQL.from_config()``, and starts even when a required datasource fails (``/ready`` then says not ready).
"""
import os
import sqlite3

import pytest
from fastapi.testclient import TestClient

from nl2sql import NL2SQL
from nl2sql.testing.config import make_config
from nl2sql_api.main import app


def _project(tmp_path, *, required):
    with sqlite3.connect(tmp_path / "shop.db") as connection:
        connection.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT)")
    make_config(tmp_path, datasources=[
        {"id": "shop", "type": "sqlite", "connection": {"database": "shop.db"}},
        {"id": "lost", "type": "sqlite", "connection": {"database": "no/such/folder/lost.db"},
         "required": required},
    ], api={"auth": "static", "static_role": "viewer"})
    return tmp_path / "nl2sql.yaml"


def test_the_api_starts_with_a_failed_required_datasource_and_is_not_ready(tmp_path, monkeypatch):
    """a restart loop would hide the reason; the API serves, `/ready` is 503 and an admin reads why."""
    from nl2sql_api.routes import health

    monkeypatch.setenv("NL2SQL_CONFIG", str(_project(tmp_path, required=True)))
    health.forget_ready(app)
    try:
        with TestClient(app) as client:
            assert client.get("/api/v1/health").status_code == 200
            ready = client.get("/api/v1/ready")
            assert ready.status_code == 503 and ready.json()["status"] == "not_ready"
    finally:
        health.forget_ready(app)
        if hasattr(app.state, "engine"):
            del app.state.engine


def test_the_api_starts_degraded_from_its_config_and_leaves_the_env_file_to_serve(tmp_path, monkeypatch):
    config = _project(tmp_path, required=False)
    (tmp_path / ".env").write_text("NL2SQL_API_TEST_FROM_FILE=file\nNL2SQL_API_TEST_SET=file\n", encoding="utf-8")
    monkeypatch.setenv("NL2SQL_CONFIG", str(config))
    monkeypatch.delenv("NL2SQL_API_TEST_FROM_FILE", raising=False)
    monkeypatch.setenv("NL2SQL_API_TEST_SET", "shell")

    try:
        with TestClient(app) as client:
            engine = app.state.engine
            assert engine.config.folder == tmp_path.absolute()
            assert engine.config.api.static_role == "viewer"
            assert client.get("/api/v1/health").status_code == 200
        assert engine.closed
        # The lifespan loads no .env: `nl2sql-api serve` does, and a hosted one never does.
        assert "NL2SQL_API_TEST_FROM_FILE" not in os.environ
        assert os.environ["NL2SQL_API_TEST_SET"] == "shell"
    finally:
        if hasattr(app.state, "engine"):
            del app.state.engine


def test_the_api_logs_at_the_level_its_environment_names(tmp_path, monkeypatch):
    import logging

    config = _project(tmp_path, required=False)
    monkeypatch.setenv("NL2SQL_CONFIG", str(config))
    monkeypatch.setenv("NL2SQL_LOG_LEVEL", "DEBUG")
    root = logging.getLogger()
    level, handlers = root.level, root.handlers[:]

    try:
        with TestClient(app):
            assert root.level == logging.DEBUG
    finally:
        root.setLevel(level)
        root.handlers[:] = handlers
        if hasattr(app.state, "engine"):
            del app.state.engine


def test_a_hosted_config_served_without_the_hosted_state_refuses_to_start(tmp_path, monkeypatch):
    """`uvicorn nl2sql_api.main:app` over `mode: hosted` would serve a public demo with no sessions or limits."""
    from nl2sql_api.identity import StartRefused
    from nl2sql_api.main import create_app

    make_config(tmp_path, mode="hosted", policies={"roles": {"viewer": {"description": "role", "allow": ["*"]}},
                                                   "default_role": "viewer"},
                api={"auth": "static", "static_role": "viewer"})
    monkeypatch.setenv("NL2SQL_CONFIG", str(tmp_path / "nl2sql.yaml"))
    with pytest.raises(StartRefused, match="nl2sql-api serve"):
        with TestClient(create_app()):
            pass


def test_api_purges_at_startup(tmp_path, monkeypatch):
    """Retention runs when the API starts (and once a day after): old traces and results go."""
    import time

    monkeypatch.setenv("NL2SQL_CONFIG", str(_project(tmp_path, required=False)))
    traces = tmp_path / ".nl2sql" / "traces"
    results = tmp_path / ".nl2sql" / "results" / "default" / "old-run"
    traces.mkdir(parents=True)
    results.mkdir(parents=True)
    old_trace = traces / "20200101T000000000000Z_old-run.json"
    new_trace = traces / "20990101T000000000000Z_new-run.json"
    old_result = results / "manifest.json"
    for path in (old_trace, new_trace, old_result):
        path.write_text("{}", encoding="utf-8")
    then = time.time() - 30 * 86400
    for path in (old_trace, old_result):
        os.utime(path, (then, then))

    try:
        with TestClient(app):
            assert not old_trace.exists() and not old_result.exists()
            assert new_trace.exists()
    finally:
        if hasattr(app.state, "engine"):
            del app.state.engine


def test_api_lifespan_runs_the_scheduler(tmp_path, monkeypatch):
    """The API calls ``engine.dashboards.run_scheduled()`` every minute while it serves."""
    import time

    from nl2sql.api.dashboards_api import DashboardsAPI

    calls = []
    monkeypatch.setenv("NL2SQL_CONFIG", str(_project(tmp_path, required=False)))
    monkeypatch.setattr(DashboardsAPI, "run_scheduled", lambda self, now=None: calls.append(now) or [])
    monkeypatch.setattr("nl2sql_api.main.SCHEDULE_INTERVAL_S", 0.01)

    try:
        with TestClient(app):
            deadline = time.time() + 5
            while len(calls) < 2 and time.time() < deadline:
                time.sleep(0.01)
            ran = len(calls)
        time.sleep(0.1)
        assert ran >= 2, "it runs again and again while the API serves"
        assert len(calls) <= ran + 1, "and stops with the API"
    finally:
        if hasattr(app.state, "engine"):
            del app.state.engine


def test_the_scheduler_runs_until_shutdown_and_survives_a_failure():
    import asyncio
    from types import SimpleNamespace

    from nl2sql_api.main import run_schedules

    calls = []

    def run_scheduled():
        calls.append(1)
        if len(calls) == 1:
            raise OSError("disk busy")
        return []

    engine = SimpleNamespace(dashboards=SimpleNamespace(run_scheduled=run_scheduled))

    async def run():
        task = asyncio.create_task(run_schedules(engine, interval_s=0.01))
        while len(calls) < 3:
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert len(calls) >= 3


def test_the_daily_purge_runs_until_shutdown_and_survives_a_failure():
    import asyncio
    from types import SimpleNamespace

    from nl2sql_api.main import purge_daily

    calls = []

    def purge():
        calls.append(1)
        if len(calls) == 1:
            raise OSError("disk busy")
        return {"traces": 0, "results": 0}

    engine = SimpleNamespace(storage=SimpleNamespace(purge=purge))

    async def run():
        task = asyncio.create_task(purge_daily(engine, interval_s=0.01))
        while len(calls) < 3:
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert len(calls) >= 3


def test_a_failure_other_than_not_ready_closes_the_engine_and_raises(tmp_path, monkeypatch):
    closed = []
    monkeypatch.setenv("NL2SQL_CONFIG", str(_project(tmp_path, required=False)))
    monkeypatch.setattr(NL2SQL, "check", lambda self: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(NL2SQL, "close", lambda self: closed.append(True))

    with pytest.raises(RuntimeError, match="boom"):
        with TestClient(app):
            pass
    assert closed == [True]
