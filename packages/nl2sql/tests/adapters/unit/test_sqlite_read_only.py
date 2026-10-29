"""A SQLite datasource is always opened read-only, so the driver refuses a write.

The engine never writes, so there is no setting: a hosted public demo cannot
be talked into changing the data it shows, whatever gets past the policy and
the validator.
"""
import sqlite3

import pytest
from pydantic import ValidationError
from sqlalchemy import text

from nl2sql.adapters.sqlite.adapter import SqliteAdapter, SqliteConnection
from nl2sql_sdk import Limits


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "sample.sqlite"
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE Album (AlbumId INTEGER PRIMARY KEY, Title TEXT)")
        con.execute("INSERT INTO Album VALUES (1, 'Let There Be Rock')")
    return path


def test_sqlite_reads_but_refuses_to_write(database):
    adapter = SqliteAdapter("demo", SqliteConnection(database=database.as_posix()), Limits())

    with adapter.engine.connect() as conn:
        assert conn.execute(text("SELECT Title FROM Album")).scalar() == "Let There Be Rock"
        with pytest.raises(Exception) as refused:
            conn.execute(text("DELETE FROM Album"))

    assert "readonly" in str(refused.value).lower()


def test_a_file_that_is_not_a_database_fails_the_connection_test(tmp_path):
    # SQLite opens any file lazily and only complains on the first read of it.
    notes = tmp_path / "notes.txt"
    notes.write_text("not a database", encoding="utf-8")
    adapter = SqliteAdapter("notes", SqliteConnection(database=notes.as_posix()), Limits())

    check = adapter.test_connection()

    assert not check.ok and check.stage == "database"


def test_a_real_database_passes_the_connection_test(database):
    adapter = SqliteAdapter("demo", SqliteConnection(database=database.as_posix()), Limits())

    assert adapter.test_connection().ok


def test_there_is_no_read_only_setting():
    with pytest.raises(ValidationError):
        SqliteConnection(database="x.sqlite", options={"read_only": True})
