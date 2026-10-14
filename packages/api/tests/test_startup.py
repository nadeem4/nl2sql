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
