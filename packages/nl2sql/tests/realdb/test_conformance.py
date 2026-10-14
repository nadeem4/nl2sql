"""The conformance kit (``nl2sql_sdk.testing``) run on PostgreSQL, MySQL and SQL Server in the test bed.

docs/architecture/phase2.md, section 7, "The conformance kit". The seed runs
with the server's admin login, in the sample database (MySQL's schemas are
databases). ``limited_login`` is a second login, ``nl2sql_kit``, whose
password holds ``@ : / # %`` and which may read the seed's first schema only.
"""
from __future__ import annotations

import os

import pytest

from nl2sql.adapters.mssql.adapter import MssqlAdapter
from nl2sql.adapters.mysql.adapter import MysqlAdapter
from nl2sql.adapters.postgres.adapter import PostgresAdapter
from nl2sql_sdk.testing import AdapterConformance

from .servers import require

KIT_LOGIN = "nl2sql_kit"
KIT_PASSWORD = "Kit1-p@ss:w/rd#50%"

# A query that runs well past the given seconds (see test_enforcement.py for MySQL's).
_MYSQL_SLOW = ("SELECT COUNT(*) FROM film a CROSS JOIN film b CROSS JOIN film c "
               "WHERE MD5(CONCAT(a.title, b.title, c.title)) LIKE 'zzzz%'")


class RealServerConformance(AdapterConformance):
    """The fixtures every server shares: the subclass names the server and its adapter."""

    name = ""
    adapter_cls = None

    @pytest.fixture
    def real(self):
        return require(self.name, os.environ)

    @pytest.fixture
    def connection(self, real):
        return self.adapter_cls.connection_model(**real.connection())

    @pytest.fixture
    def adapter_factory(self):
        return lambda connection, limits: self.adapter_cls(f"kit_{self.name}", connection, limits)

    @pytest.fixture
    def admin(self, real):
        engine = real.engine()
        yield engine
        engine.dispose()

    def _execute(self, admin, statements):
        with admin.connect().execution_options(isolation_level="AUTOCOMMIT", no_parameters=True) as conn:
            for statement in statements:
                conn.exec_driver_sql(statement)

    @pytest.fixture
    def seed(self, admin):
        return lambda statements: self._execute(admin, statements)

    def _login_sql(self):
        raise NotImplementedError

    @pytest.fixture
    def limited_login(self, seeded, admin, real):
        self._execute(admin, self._login_sql())
        block = {**real.connection(), "user": KIT_LOGIN, "password": KIT_PASSWORD}
        if self.name == "mysql":
            block["database"] = self.schemas[0]
        return self.adapter_cls.connection_model(**block)


class TestPostgresConformance(RealServerConformance):
    name, adapter_cls = "postgres", PostgresAdapter

    @pytest.fixture
    def sleep_sql(self):
        return lambda seconds: f"SELECT pg_sleep({seconds})"

    def _login_sql(self):
        return [
            f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{KIT_LOGIN}') THEN "
            f"CREATE ROLE {KIT_LOGIN} LOGIN PASSWORD '{KIT_PASSWORD}'; END IF; END $$",
            f"GRANT USAGE ON SCHEMA kit_a TO {KIT_LOGIN}",
            f"GRANT SELECT ON ALL TABLES IN SCHEMA kit_a TO {KIT_LOGIN}",
        ]


class TestMysqlConformance(RealServerConformance):
    name, adapter_cls = "mysql", MysqlAdapter
    # MySQL has no uuid type.
    column_types = {"uuid": "CHAR(36)"}

    @pytest.fixture
    def sleep_sql(self):
        return lambda seconds: _MYSQL_SLOW

    def _login_sql(self):
        return [
            f"CREATE USER IF NOT EXISTS '{KIT_LOGIN}'@'%' IDENTIFIED BY '{KIT_PASSWORD}'",
            f"GRANT SELECT ON kit_a.* TO '{KIT_LOGIN}'@'%'",
        ]


class TestMssqlConformance(RealServerConformance):
    name, adapter_cls = "mssql", MssqlAdapter
    # SQL Server 2022 has no json type.
    column_types = {"json": "NVARCHAR(MAX)"}

    @pytest.fixture
    def sleep_sql(self):
        return lambda seconds: f"WAITFOR DELAY '00:00:{seconds:02d}'"

    def _login_sql(self):
        return [
            f"IF SUSER_ID('{KIT_LOGIN}') IS NULL "
            f"CREATE LOGIN {KIT_LOGIN} WITH PASSWORD = N'{KIT_PASSWORD}', CHECK_POLICY = OFF",
            f"IF USER_ID('{KIT_LOGIN}') IS NULL CREATE USER {KIT_LOGIN} FOR LOGIN {KIT_LOGIN}",
            f"GRANT SELECT ON SCHEMA::kit_a TO {KIT_LOGIN}",
        ]
