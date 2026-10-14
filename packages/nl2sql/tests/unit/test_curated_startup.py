"""The engine checks the ``curated`` section at startup, and a broken one stops it."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from langchain_core.embeddings import DeterministicFakeEmbedding

from nl2sql import ConfigError, Embeddings, NL2SQL
from nl2sql.testing.config import make_config

QUERY = {
    "id": "book_count",
    "description": "How many books there are",
    "questions": ["How many books are there?"],
    "sql": "SELECT COUNT(*) AS books FROM Book",
}


def _engine(tmp_path: Path, curated=None) -> NL2SQL:
    database = tmp_path / "books.sqlite"
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS Book (BookId INTEGER PRIMARY KEY, Title TEXT)")
    sections = {"datasources": [{"id": "books", "type": "sqlite", "connection": {"database": "books.sqlite"}}]}
    if curated is not None:
        sections["curated"] = curated
    cfg = make_config(tmp_path, **sections)
    return NL2SQL(cfg.model_copy(update={"embeddings": Embeddings(instance=DeterministicFakeEmbedding(size=8))}))


def test_no_curated_section_starts_with_the_feature_off(tmp_path):
    assert _engine(tmp_path)._runtime.curated is None


def test_a_good_curated_section_is_loaded(tmp_path):
    engine = _engine(tmp_path, {"datasources": {"books": {"verified_queries": [QUERY]}}})

    assert engine._runtime.curated.datasources["books"].verified_queries[0].id == "book_count"


@pytest.mark.parametrize("broken, fragment", [
    ({"datasources": {"bookz": {"verified_queries": [QUERY]}}}, "bookz"),
    ({"datasources": {"books": {"verified_queries": [dict(QUERY, sql="DELETE FROM Book")]}}}, "read-only"),
])
def test_curated_answers_that_do_not_match_the_datasources_fail_startup(tmp_path, broken, fragment):
    with pytest.raises(ConfigError, match=fragment) as caught:
        _engine(tmp_path, broken)

    assert "curated.datasources" in str(caught.value)


def test_a_duplicate_verified_query_fails_when_the_config_loads(tmp_path):
    with pytest.raises(ConfigError, match="book_count"):
        _engine(tmp_path, {"datasources": {"books": {"verified_queries": [QUERY, QUERY]}}})
