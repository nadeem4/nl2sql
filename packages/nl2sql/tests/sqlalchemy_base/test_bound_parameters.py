"""Bound parameters through the SQLAlchemy base adapter.

A verified query's SQL carries ``:name`` placeholders and its values travel in
``AdapterRequest.parameters``. The values are bound by the driver, never
pasted into the SQL text, so a value cannot change what the query does.
"""
import sqlite3

import pytest

from nl2sql_sdk.sqlalchemy import SQLAlchemyAdapter
from nl2sql_sdk.capabilities import DatasourceCapability
from nl2sql_sdk import ConnectionModel, Limits
from nl2sql_sdk.contracts import AdapterRequest


class _File(ConnectionModel):
    database: str


class _SQLiteAdapter(SQLAlchemyAdapter):
    title = "Test SQLite"
    connection_model = _File

    def url(self, connection):
        return f"sqlite:///{connection.database}"

    def get_dialect(self) -> str:
        return "sqlite"


@pytest.fixture()
def db_path(tmp_path):
    path = tmp_path / "bound.db"
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE x (id INTEGER PRIMARY KEY, name TEXT)")
        conn.executemany("INSERT INTO x (id, name) VALUES (?, ?)", [(1, "a"), (2, "b"), (3, "c")])
        conn.commit()
    finally:
        conn.close()
    return path


@pytest.fixture()
def adapter(db_path):
    return _SQLiteAdapter("ds", _File(database=str(db_path)), Limits())


def _request(sql, parameters=None):
    return AdapterRequest(plan_type="sql", payload={"sql": sql}, parameters=parameters or {})


def _row_count(db_path) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("SELECT COUNT(*) FROM x").fetchone()[0]
    finally:
        conn.close()


def test_the_base_adapter_declares_it_can_bind_parameters(adapter):
    assert DatasourceCapability.SUPPORTS_BOUND_PARAMETERS in adapter.capabilities()
    assert DatasourceCapability.SUPPORTS_SQL in adapter.capabilities()


def test_execute_binds_named_parameters(adapter):
    frame = adapter.execute(_request("SELECT id, name FROM x WHERE id >= :low ORDER BY id LIMIT :n",
                                     {"low": 2, "n": 5}))

    assert frame.success, frame.error
    assert frame.rows == [[2, "b"], [3, "c"]]


def test_execute_batches_binds_named_parameters(adapter):
    frames = list(adapter.execute_batches(_request("SELECT id FROM x WHERE id <= :high ORDER BY id",
                                                   {"high": 2}), batch_size=1))

    assert all(f.success for f in frames)
    assert [row for f in frames for row in f.rows] == [[1], [2]]


@pytest.mark.parametrize("run", ["execute", "execute_batches"])
def test_a_hostile_value_is_bound_as_a_value_and_runs_nothing(adapter, db_path, run):
    sql = "SELECT id FROM x WHERE id = :id"
    request = _request(sql, {"id": "1; DROP TABLE x"})

    if run == "execute":
        frames = [adapter.execute(request)]
    else:
        frames = list(adapter.execute_batches(request, batch_size=10))

    # The value is compared as a value: no row equals that string, and the
    # table is still there with every row.
    assert all(f.success for f in frames), [f.error for f in frames]
    assert [row for f in frames for row in f.rows] == []
    assert _row_count(db_path) == 3
    # The SQL text the adapter was handed is unchanged.
    assert request.payload["sql"] == sql


def test_without_parameters_the_call_is_todays(adapter, monkeypatch):
    """Generated SQL carries no parameters, and the driver is called exactly as before."""
    seen = []
    real_connect = adapter.engine.connect

    class _Spy:
        def __init__(self, conn):
            self._conn = conn

        def __enter__(self):
            inner = self._conn.__enter__()
            original = inner.execute

            def execute(*args, **kwargs):
                seen.append((len(args), kwargs))
                return original(*args, **kwargs)

            inner.execute = execute
            return inner

        def __exit__(self, *exc):
            return self._conn.__exit__(*exc)

    monkeypatch.setattr(adapter.engine, "connect", lambda: _Spy(real_connect()))

    frame = adapter.execute(_request("SELECT id FROM x ORDER BY id"))

    assert frame.success
    assert seen == [(1, {})]
