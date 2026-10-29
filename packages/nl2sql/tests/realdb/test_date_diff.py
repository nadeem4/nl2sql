"""``DATE_DIFF('day', start, end)`` on PostgreSQL, MySQL and SQL Server in the test bed (C-W6d).

The elapsed days with the fraction kept, the same on every database: the answers
below are computed here, not by the database under test.
"""
from __future__ import annotations

from datetime import datetime

import pytest
from sqlglot import expressions as exp

from nl2sql.datasources.registry import DatasourceRegistry
from nl2sql.pipeline.nodes.generator.node import elapsed_days
from nl2sql.registry import Registry
from nl2sql.secrets.manager import SecretManager

from .servers import datasource

# (start, end): about 3.87 days; two minutes across midnight; negative.
TIMESTAMPS = [
    ("2005-05-24 22:53:30", "2005-05-28 19:40:33"),
    ("2020-01-01 23:59:00", "2020-01-02 00:01:00"),
    ("2020-01-05 00:00:00", "2020-01-01 00:00:00"),
]


def _expected(start: str, end: str) -> float:
    fmt = "%Y-%m-%d %H:%M:%S"
    return (datetime.strptime(end, fmt) - datetime.strptime(start, fmt)).total_seconds() / 86400


@pytest.fixture
def adapter(server):
    built = DatasourceRegistry(SecretManager(), Registry.default()).register_datasource(datasource(server))
    yield built
    built.close()


def _days(adapter, kind: str, start: str, end: str | None):
    def operand(value):
        if value is None:
            return exp.Null()
        return exp.Cast(this=exp.Literal.string(value), to=exp.DataType.build(kind))

    sql = adapter.render_sql(exp.select(elapsed_days(operand(start), operand(end))))
    return adapter.execute_sql(sql).rows[0][0]


@pytest.mark.parametrize("start, end", TIMESTAMPS)
def test_elapsed_days_keep_the_fraction(adapter, start, end):
    kind = "DATETIME" if adapter.get_dialect() == "mysql" else "TIMESTAMP"

    assert float(_days(adapter, kind, start, end)) == pytest.approx(_expected(start, end), abs=1e-6)


def test_date_only_values_give_whole_days(adapter):
    assert float(_days(adapter, "DATE", "2020-01-01", "2020-01-05")) == pytest.approx(4.0)


def test_a_null_end_gives_null(adapter):
    kind = "DATETIME" if adapter.get_dialect() == "mysql" else "TIMESTAMP"

    assert _days(adapter, kind, "2020-01-01 00:00:00", None) is None
