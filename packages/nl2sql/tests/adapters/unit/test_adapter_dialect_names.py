"""Every adapter's ``get_dialect()`` is a name sqlglot knows.

The generator renders SQL with ``query.sql(dialect=adapter.get_dialect())``.
Postgres and SQL Server returned SQLAlchemy's names (``postgresql``,
``mssql``), which sqlglot rejects, so no SQL could be generated for either.
"""
from importlib.metadata import entry_points

import pytest
from sqlglot.dialects.dialect import Dialect

# One per name: an editable install next to a checkout can list a name twice.
ADAPTERS = sorted({ep.name: ep for ep in entry_points(group="nl2sql.adapters")}.values(), key=lambda ep: ep.name)


def test_every_bundled_adapter_is_checked():
    assert {ep.name for ep in ADAPTERS} >= {"duckdb", "mssql", "mysql", "postgres", "sqlite"}


@pytest.mark.parametrize("entry_point", ADAPTERS, ids=lambda ep: ep.name)
def test_get_dialect_is_a_sqlglot_dialect(entry_point):
    cls = entry_point.load()
    adapter = cls.__new__(cls)  # no connection: the dialect is a constant

    Dialect.get_or_raise(adapter.get_dialect())


@pytest.mark.parametrize("name, dialect", [
    ("postgres", "postgres"), ("mssql", "tsql"), ("mysql", "mysql"), ("duckdb", "duckdb"), ("sqlite", "sqlite"),
])
def test_bundled_adapter_dialects(name, dialect):
    cls = next(ep for ep in ADAPTERS if ep.name == name).load()

    assert cls.__new__(cls).get_dialect() == dialect
