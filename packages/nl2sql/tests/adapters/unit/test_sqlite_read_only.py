"""A SQLite datasource opened read-only refuses a write at the driver.

The demo's three sample databases are configured this way
(``nl2sql.cli.demo.datasets``), so a hosted public demo cannot be talked into
changing the data it shows, whatever gets past the policy and the validator.
"""
import sqlite3

import pytest
from sqlalchemy import text

from nl2sql.adapters.sqlite.adapter import SqliteAdapter
from nl2sql.cli.demo.datasets import DEMO_DATABASES


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "sample.sqlite"
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE Album (AlbumId INTEGER PRIMARY KEY, Title TEXT)")
        con.execute("INSERT INTO Album VALUES (1, 'Let There Be Rock')")
    return path


def _adapter(database, read_only):
    connection = {"type": "sqlite", "database": database.as_posix()}
    if read_only:
        connection["options"] = {"read_only": True}
    adapter = SqliteAdapter(datasource_id="demo", datasource_engine_type="sqlite",
                            connection_args=connection)
    adapter.connect()
    return adapter


def test_read_only_reads_but_refuses_to_write(database):
    adapter = _adapter(database, read_only=True)

    with adapter.engine.connect() as conn:
        assert conn.execute(text("SELECT Title FROM Album")).scalar() == "Let There Be Rock"
        with pytest.raises(Exception) as refused:
            conn.execute(text("DELETE FROM Album"))

    assert "readonly" in str(refused.value).lower()


def test_without_the_option_the_connection_is_writable_as_before(database):
    adapter = _adapter(database, read_only=False)

    with adapter.engine.connect() as conn:
        conn.execute(text("DELETE FROM Album"))

    assert adapter.connection_string == f"sqlite:///{database.as_posix()}"


def test_every_demo_database_is_configured_read_only():
    options = [config["connection"].get("options", {}) for _, _, config in DEMO_DATABASES]

    assert all(option.get("read_only") is True for option in options)
