"""A text column with few distinct values is indexed with all of them.

Five sample values per column meant a filter on the sixth was a guess: Chinook
has 25 genres, and "heavy metal" could not be matched to 'Heavy Metal' if that
genre was not among the five most frequent. A column with at most
``ALL_VALUES_MAX_DISTINCT`` distinct values now keeps every one; a larger one
keeps the five most frequent.
"""
import sqlite3

import pytest

from nl2sql_sdk.sqlalchemy import ALL_VALUES_MAX_DISTINCT
from nl2sql.adapters.sqlite.adapter import SqliteAdapter, SqliteConnection
from nl2sql_sdk import IndexScope, Limits


def _snapshot(path):
    adapter = SqliteAdapter("t", SqliteConnection(database=path.as_posix()), Limits())
    snapshot = adapter.fetch_schema_snapshot(IndexScope(sample_values=True))
    [table] = snapshot.metadata.tables.values()
    return {name: column.statistics for name, column in table.columns.items()}


@pytest.fixture
def stats(tmp_path):
    path = tmp_path / "values.sqlite"
    small = [f"genre {i:02d}" for i in range(ALL_VALUES_MAX_DISTINCT)]
    large = [f"name {i:03d}" for i in range(ALL_VALUES_MAX_DISTINCT + 1)]
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE T (id INTEGER PRIMARY KEY, genre TEXT, name TEXT, n INTEGER)")
        rows = [(i, small[i % len(small)], large[i % len(large)], i) for i in range(200)]
        # 'genre 07' is the most frequent value of both columns' kind.
        rows += [(1000 + i, "genre 07", "name 007", 0) for i in range(5)]
        con.executemany("INSERT INTO T VALUES (?, ?, ?, ?)", rows)
    return _snapshot(path)


def test_a_small_text_column_keeps_every_distinct_value(stats):
    genre = stats["genre"]

    assert genre.distinct_count == ALL_VALUES_MAX_DISTINCT
    assert sorted(genre.sample_values) == [f"genre {i:02d}" for i in range(ALL_VALUES_MAX_DISTINCT)]
    assert genre.sample_values[0] == "genre 07"  # most frequent first


def test_a_large_text_column_keeps_the_five_most_frequent(stats):
    name = stats["name"]

    assert name.distinct_count == ALL_VALUES_MAX_DISTINCT + 1
    assert len(name.sample_values) == 5
    assert name.sample_values[0] == "name 007"


def test_values_are_in_a_stable_order(tmp_path):
    """Ties are broken by value, so a rebuilt index renders the same prompt."""
    path = tmp_path / "ties.sqlite"
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE T (id INTEGER PRIMARY KEY, c TEXT)")
        con.executemany("INSERT INTO T VALUES (?, ?)", [(i, v) for i, v in enumerate("dbca")])

    assert _snapshot(path)["c"].sample_values == ["a", "b", "c", "d"]


def test_a_numeric_column_has_no_sample_values(stats):
    assert stats["n"].sample_values == []
