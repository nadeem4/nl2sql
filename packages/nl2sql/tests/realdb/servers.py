"""Where the test bed's servers are, read from the environment, for the ``realdb`` tests.

docs/architecture/phase2.md, section 3, "The realdb marker and fixtures".

| Variable | Meaning |
|---|---|
| ``REALDB=compose`` | The compose servers on 127.0.0.1, at the published ports, with the passwords in ``TESTBED_POSTGRES_PASSWORD``, ``TESTBED_MYSQL_PASSWORD`` and ``TESTBED_MSSQL_PASSWORD`` (``testbed/passwords.py`` makes them) |
| ``REALDB_POSTGRES``, ``REALDB_MYSQL``, ``REALDB_MSSQL`` | A URL for one server; wins over ``REALDB=compose`` |
| ``REALDB_REQUIRED=1`` | A missing server is a failure, not a skip |

Test code may name dialects and use SQLAlchemy directly: the boundary tests
cover ``packages/*/src``, not the tests.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import URL, Engine, make_url

from nl2sql.adapters.mssql.adapter import newest_sql_server_driver
from nl2sql.config import Datasource

SERVERS = ("postgres", "mysql", "mssql")
READER = "nl2sql_reader"
FIX = ("Run `python testbed/passwords.py > testbed/.env` and "
       "`docker compose -f testbed/docker-compose.yml up -d --wait`, then set `REALDB=compose` with "
       "testbed/.env's variables in the environment, or set REALDB_{upper} to the server's URL.")


@dataclass(frozen=True)
class Kind:
    title: str
    drivername: str
    port: int
    user: str
    sample_database: str
    sample_table: str        # a table of the sample, with the row count below
    sample_rows: int


KINDS: Dict[str, Kind] = {
    "postgres": Kind("PostgreSQL", "postgresql+psycopg2", 15432, "postgres", "pagila", "film", 1000),
    "mysql": Kind("MySQL", "mysql+pymysql", 13306, "root", "sakila", "film", 1000),
    "mssql": Kind("SQL Server", "mssql+pyodbc", 11433, "sa", "AdventureWorksLT2022", "SalesLT.Product", 295),
}


@dataclass(frozen=True)
class RealServer:
    """One test-bed server: ``name`` is the adapter type, ``url`` its admin login."""

    name: str
    url: URL

    @property
    def kind(self) -> Kind:
        return KINDS[self.name]

    @property
    def sample_database(self) -> str:
        return self.kind.sample_database

    def wide_database(self, schema: str) -> str:
        """The database holding wide-schema ``schema``: MySQL's schemas are databases."""
        if self.name != "mysql":
            return "wide"
        return "wide_audit" if schema == "audit" else f"wide_s{schema.rsplit('_', 1)[1]}"

    def wide_table(self, schema: str, table: str) -> str:
        """Wide-schema ``schema.table``, quoted, as written from inside ``wide_database(schema)``."""
        if self.name == "mysql":
            return f"`{table}`"
        return f"{schema}.[{table}]" if self.name == "mssql" else f'{schema}."{table}"'

    def sqlalchemy_url(self, database: Optional[str] = None, *, user: Optional[str] = None,
                       password: Optional[str] = None) -> URL:
        url = self.url.set(database=database or self.sample_database)
        if user is not None:
            url = url.set(username=user, password=password if password is not None else self.url.password)
        if self.name == "mssql":
            url = url.update_query_dict({"driver": newest_sql_server_driver(), "TrustServerCertificate": "yes"})
        return url

    def engine(self, database: Optional[str] = None, **login: Any) -> Engine:
        return create_engine(self.sqlalchemy_url(database, **login))

    def connection(self, database: Optional[str] = None) -> Dict[str, Any]:
        """The ``connection`` block of a nl2sql datasource for ``database`` on this server."""
        block: Dict[str, Any] = {"host": self.url.host, "port": self.url.port, "user": self.url.username,
                                 "password": self.url.password, "database": database or self.sample_database}
        if self.name == "mssql":
            # No driver: the adapter picks the newest one installed, which these tests exercise.
            block.update(trust_server_certificate=True)
        return block


def resolve(name: str, environ: Mapping[str, str]) -> Optional[RealServer]:
    """The server called ``name``, or None when the environment names none."""
    kind = KINDS[name]
    url = environ.get(f"REALDB_{name.upper()}")
    if url:
        parsed = make_url(url)
        return RealServer(name, parsed.set(drivername=kind.drivername, database=None))
    password = environ.get(password_variable(name))
    if environ.get("REALDB") == "compose" and password:
        return RealServer(name, URL.create(kind.drivername, username=kind.user, password=password,
                                           host="127.0.0.1", port=kind.port))
    return None


def password_variable(name: str) -> str:
    return f"TESTBED_{name.upper()}_PASSWORD"


def require(name: str, environ: Mapping[str, str]) -> RealServer:
    """``resolve``, or skip loudly with the fix, or fail under ``REALDB_REQUIRED=1``."""
    server = resolve(name, environ)
    if server is not None:
        return server
    message = f"realdb: no {KINDS[name].title}. "
    if environ.get("REALDB") == "compose":
        message += f"REALDB=compose needs {password_variable(name)} in the environment. "
    message += FIX.format(upper=name.upper())
    if environ.get("REALDB_REQUIRED") == "1":
        pytest.fail(message + " (REALDB_REQUIRED=1 makes this a failure.)")
    pytest.skip(message)


def datasource(server: RealServer, database: Optional[str] = None, **fields: Any) -> Datasource:
    """A Datasource for one test-bed database (the server's sample by default), built from the server's URL."""
    database = database or server.sample_database
    return Datasource(id=f"{server.name}_{database}".lower(), type=server.name,
                      connection=server.connection(database), **fields)
