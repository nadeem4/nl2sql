"""The conformance kit (``nl2sql_sdk.testing``) run on the two in-process built-ins, SQLite and DuckDB.

docs/architecture/phase2.md, section 7, "The conformance kit". PostgreSQL,
MySQL and SQL Server run it in ``tests/realdb/test_conformance.py``.
"""
from __future__ import annotations

import sqlite3
from contextlib import closing

import duckdb
import pytest

from nl2sql.adapters.duckdb.adapter import DuckdbAdapter, DuckdbConnection
from nl2sql.adapters.sqlite.adapter import SqliteAdapter, SqliteConnection
from nl2sql_sdk import DatasourceCapability, Limits
from nl2sql_sdk.testing import AdapterConformance


class TestSqliteConformance(AdapterConformance):
    schemas = ("main",)

    @pytest.fixture
    def connection(self, tmp_path):
        return SqliteConnection(database=str(tmp_path / "kit.sqlite"))

    @pytest.fixture
    def adapter_factory(self):
        return lambda connection, limits: SqliteAdapter("kit", connection, limits)

    @pytest.fixture
    def seed(self, connection):
        def run(statements):
            with closing(sqlite3.connect(connection.database)) as conn:
                for statement in statements:
                    conn.execute(statement)
                conn.commit()
        return run

    @pytest.fixture
    def sleep_sql(self):
        # SQLite has no sleep: a count that never ends.
        return lambda seconds: "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT COUNT(*) FROM c"


class TestDuckdbConformance(AdapterConformance):

    @pytest.fixture
    def connection(self, tmp_path):
        return DuckdbConnection(database=str(tmp_path / "kit.duckdb"))

    @pytest.fixture
    def adapter_factory(self):
        return lambda connection, limits: DuckdbAdapter("kit", connection, limits)

    @pytest.fixture
    def seed(self, connection):
        def run(statements):
            with closing(duckdb.connect(connection.database)) as conn:
                for statement in statements:
                    conn.execute(statement)
        return run

    @pytest.fixture
    def sleep_sql(self):
        return lambda seconds: "SELECT SUM(range) FROM range(10000000000000)"


class WritableSqlite(SqliteAdapter):
    """SQLite as an adapter that cannot make its session read-only would declare it."""

    def capabilities(self):
        return super().capabilities() - {DatasourceCapability.ENFORCES_READ_ONLY}


def test_kit_reports_undeclared_read_only_as_skipped(tmp_path):
    connection = SqliteConnection(database=str(tmp_path / "kit.sqlite"))

    def build(on=None, limits=None):
        return WritableSqlite("kit", on or connection, limits or Limits())

    with pytest.raises(pytest.skip.Exception, match="WritableSqlite does not declare ENFORCES_READ_ONLY"):
        AdapterConformance().test_writes_fail_when_read_only_is_declared(build)
