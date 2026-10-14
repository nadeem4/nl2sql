"""A query the database rejects comes back as a ``ResultFrame`` error, not an exception.

``SQLAlchemyAdapter.execute`` used to let the driver's exception escape, so
the executor node reported every database error as a CRITICAL
``EXECUTOR_CRASH`` and the sub-query ended, even for an unknown column the
planner could have fixed. The adapter, which is the only place that knows its
driver, now says whether the query itself was at fault (``retryable``) and
gives a short message with no SQL echo, no driver dump and no stack trace.
"""
import sqlite3

import pytest
from sqlalchemy import exc as sa_exc

from nl2sql_sdk.sqlalchemy import SQLAlchemyAdapter
from nl2sql.adapters.sqlite.adapter import SqliteAdapter, SqliteConnection
from nl2sql_sdk import Limits
from nl2sql_sdk.contracts import AdapterRequest


@pytest.fixture
def adapter(tmp_path):
    path = tmp_path / "sample.sqlite"
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE Album (AlbumId INTEGER PRIMARY KEY, Title TEXT)")
        con.execute("INSERT INTO Album VALUES (1, 'Let There Be Rock')")
    return SqliteAdapter("demo", SqliteConnection(database=path.as_posix()), Limits())


def _run(adapter, sql):
    return adapter.execute(AdapterRequest(plan_type="sql", payload={"sql": sql}))


def _assert_safe(message: str):
    assert message
    assert len(message) <= 300
    for leak in ("[SQL:", "Traceback", "sqlalche.me", "(sqlite3."):
        assert leak not in message


@pytest.mark.parametrize(
    "sql, words",
    [
        ("SELECT a.Missing FROM Album a", "no such column"),
        ("SELECT * FROM Albums", "no such table"),
        ("SELEC 1", "syntax error"),
        ("SELECT ABS(1, 2)", "wrong number of arguments"),
    ],
    ids=["unknown column", "unknown table", "syntax", "wrong arity"],
)
def test_a_mistake_in_the_query_is_retryable_with_a_short_message(adapter, sql, words):
    frame = _run(adapter, sql)

    assert frame.success is False
    assert frame.error.retryable is True
    assert words in frame.error.safe_message
    _assert_safe(frame.error.safe_message)


def test_a_refusal_is_not_retryable_and_names_nothing(adapter):
    # The demo databases are opened read-only: a write is refused at the
    # driver, and no new plan changes that.
    frame = _run(adapter, "INSERT INTO Album VALUES (2, 'x')")

    assert frame.success is False
    assert frame.error.retryable is False
    assert "readonly" not in frame.error.safe_message
    _assert_safe(frame.error.safe_message)


def test_a_good_query_still_returns_rows(adapter):
    frame = _run(adapter, "SELECT Title FROM Album")

    assert frame.success is True
    assert frame.rows == [["Let There Be Rock"]]


class _DriverError(Exception):
    def __init__(self, message, sqlstate=None):
        super().__init__(message)
        self.pgcode = sqlstate


def _wrapped(kind, message, sqlstate=None):
    return kind("SELECT secret FROM t", {}, _DriverError(message, sqlstate))


@pytest.mark.parametrize(
    "error, retryable",
    [
        (_wrapped(sa_exc.ProgrammingError, 'column "x" does not exist\nLINE 1: SELECT x', "42703"), True),
        (_wrapped(sa_exc.ProgrammingError, "syntax error at or near SELEC", "42601"), True),
        (_wrapped(sa_exc.DataError, "invalid input syntax for type integer", "22P02"), True),
        (_wrapped(sa_exc.ProgrammingError, "permission denied for table t", "42501"), False),
        (_wrapped(sa_exc.OperationalError, "canceling statement due to statement timeout", "57014"), False),
        (_wrapped(sa_exc.OperationalError, "connection refused"), False),
        (_wrapped(sa_exc.ProgrammingError, "The SELECT permission was denied on the object"), False),
        (_wrapped(sa_exc.ProgrammingError, "Invalid column name 'x'."), True),
        (_wrapped(sa_exc.DataError, "Conversion failed when converting"), True),
    ],
    ids=["unknown column", "syntax", "type mismatch", "permission (sqlstate)", "timeout",
         "connection", "permission (message)", "unknown column (no sqlstate)", "conversion"],
)
def test_the_base_adapter_classifies_by_sqlstate_then_by_kind(error, retryable):
    adapter = SQLAlchemyAdapter.__new__(SQLAlchemyAdapter)
    adapter.datasource_id = "ds"

    frame = adapter.query_failed(error)

    assert frame.success is False
    assert frame.error.retryable is retryable
    _assert_safe(frame.error.safe_message)
    # Only the driver's first line, and only when the query was at fault.
    assert "LINE 1" not in frame.error.safe_message
    assert "SELECT secret" not in frame.error.safe_message
