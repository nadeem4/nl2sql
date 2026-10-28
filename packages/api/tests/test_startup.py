"""``nl2sql-api`` starts from ``nl2sql.yaml`` and calls ``engine.check()`` before it serves.

docs/architecture/configuration_v2.md, "Lifecycle" and the ``nl2sql-api`` row of
"Impact on the consumers": the lifespan loads the ``.env`` next to
``NL2SQL_CONFIG``'s file (never overriding a variable already set), builds the
engine with ``NL2SQL.from_config()``, and refuses to start on ``EngineNotReady``.
"""
import os
import sqlite3

import pytest
from fastapi.testclient import TestClient

from nl2sql import EngineNotReady
from nl2sql.testing.config import make_config
from nl2sql_api.main import app


def _project(tmp_path, *, required):
    with sqlite3.connect(tmp_path / "shop.db") as connection:
        connection.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT)")
    make_config(tmp_path, datasources=[
        {"id": "shop", "type": "sqlite", "connection": {"database": "shop.db"}},
        {"id": "lost", "type": "sqlite", "connection": {"database": "no/such/folder/lost.db"},
         "required": required},
    ], api={"role": "viewer"})
    return tmp_path / "nl2sql.yaml"


def test_entry_points_refuse_to_start_when_a_required_datasource_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("NL2SQL_CONFIG", str(_project(tmp_path, required=True)))

    with pytest.raises(EngineNotReady, match="lost"):
        with TestClient(app):
            pass


def test_the_api_starts_degraded_from_its_config_and_loads_the_env_file(tmp_path, monkeypatch):
    config = _project(tmp_path, required=False)
    (tmp_path / ".env").write_text("NL2SQL_API_TEST_FROM_FILE=file\nNL2SQL_API_TEST_SET=file\n", encoding="utf-8")
    monkeypatch.setenv("NL2SQL_CONFIG", str(config))
    monkeypatch.delenv("NL2SQL_API_TEST_FROM_FILE", raising=False)
    monkeypatch.setenv("NL2SQL_API_TEST_SET", "shell")

    try:
        with TestClient(app) as client:
            engine = app.state.engine
            assert engine.config.folder == tmp_path.absolute()
            assert engine.config.api.role == "viewer"
            assert client.get("/api/v1/health").status_code == 200
        assert engine.closed
        assert os.environ["NL2SQL_API_TEST_FROM_FILE"] == "file"
        assert os.environ["NL2SQL_API_TEST_SET"] == "shell"
    finally:
        os.environ.pop("NL2SQL_API_TEST_FROM_FILE", None)
        if hasattr(app.state, "engine"):
            del app.state.engine


def test_the_api_logs_at_the_level_its_env_file_names(tmp_path, monkeypatch):
    import logging

    config = _project(tmp_path, required=False)
    (tmp_path / ".env").write_text("NL2SQL_LOG_LEVEL=DEBUG\n", encoding="utf-8")
    monkeypatch.setenv("NL2SQL_CONFIG", str(config))
    # Set, then deleted, so whatever the .env adds is removed afterwards.
    monkeypatch.setenv("NL2SQL_LOG_LEVEL", "")
    monkeypatch.delenv("NL2SQL_LOG_LEVEL")
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
    """The API calls ``engine.dashboards.run_scheduled()`` every minute while it serves (wave_c.md, D132)."""
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
