"""The test bed is what it says: each sample and the wide schema are loaded, and the reader login is limited.

docs/architecture/phase2.md, section 3.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from .servers import READER

TESTBED = Path(__file__).resolve().parents[4] / "testbed"


@pytest.fixture(scope="module")
def wide():
    """The generated wide schema, as data, from the generator the ``samples`` service ran."""
    sys.path.insert(0, str(TESTBED))
    try:
        import wide_schema
    finally:
        sys.path.remove(str(TESTBED))
    return wide_schema.build(7)


def _scalar(engine, sql: str):
    try:
        with engine.connect() as connection:
            return connection.execute(text(sql)).scalar()
    finally:
        engine.dispose()


def test_each_sample_is_loaded(server):
    kind = server.kind
    assert _scalar(server.engine(), f"SELECT COUNT(*) FROM {kind.sample_table}") == kind.sample_rows


def test_the_wide_schema_is_loaded(server, wide):
    numbered = [s for s in wide if s.name != "audit"]
    if server.name == "mysql":
        databases = ", ".join(f"'{server.wide_database(s.name)}'" for s in numbered)
        where = f"table_schema IN ({databases})"
    else:
        where = "table_schema IN (" + ", ".join(f"'{s.name}'" for s in numbered) + ")"
    count = f"SELECT COUNT(*) FROM information_schema.tables WHERE table_type = 'BASE TABLE' AND {where}"
    assert _scalar(server.engine(server.wide_database(numbered[0].name)), count) == 1200

    big = next(t for s in numbered for t in s.tables if t.rows == 1_000_000)
    schema = next(s for s in numbered if big in s.tables)
    assert _scalar(server.engine(server.wide_database(schema.name)),
                   f"SELECT COUNT(*) FROM {server.wide_table(schema.name, big.name)}") == 1_000_000


def test_reader_login_cannot_read_the_wide_schema(server, wide):
    password = server.url.password
    kind = server.kind
    # The login works: it reads the sample. So a failure below is a refusal, not a bad password.
    reader = server.engine(user=READER, password=password)
    assert _scalar(reader, f"SELECT COUNT(*) FROM {kind.sample_table}") == kind.sample_rows

    schema = wide[0]
    assert _scalar(server.engine(server.wide_database(schema.name)),
                   f"SELECT COUNT(*) FROM {server.wide_table(schema.name, schema.tables[0].name)}") is not None
    with pytest.raises(DBAPIError) as refused:
        _scalar(server.engine(server.wide_database(schema.name), user=READER, password=password),
                f"SELECT COUNT(*) FROM {server.wide_table(schema.name, schema.tables[0].name)}")
    error = str(refused.value).lower()
    assert any(refusal in error for refusal in ("denied", "cannot open database")), error


def test_an_engine_on_each_server_is_ready(engine_on):
    report = engine_on().check()
    assert [d.status for d in report.datasources] == ["ready"]
