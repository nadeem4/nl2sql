"""``nl2sql curated test`` and the facade it calls, ``engine.curated.test()``.

Each verified query runs once against its database, with each param's default
or else its example bound, read-only through the datasource's adapter. A query
that no longer runs (a column renamed under it, say) fails, and the command
exits non-zero, so it can run in CI after a schema change. The verified
queries are the ``curated`` section of the config ``--config`` names.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from nl2sql import NL2SQL, ConfigError, load_config
from nl2sql.cli.main import app
from nl2sql.testing.config import make_config

runner = CliRunner()
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

TITLES = {
    "id": "titles_by_year",
    "description": "Book titles published in a year",
    "questions": ["Which books came out in {year}?", "First {n} books of {year}"],
    "params": {
        "year": {"type": "int", "min": 1900, "max": 2100, "example": 2001},
        "n": {"type": "int", "default": 2, "min": 1, "max": 100},
    },
    "sql": "SELECT Title FROM Book WHERE Year = :year ORDER BY Title LIMIT :n",
}
COUNT = {"id": "book_count", "description": "How many books", "questions": ["How many books?"],
         "sql": "SELECT COUNT(*) AS books FROM Book"}


def _project(tmp_path: Path, title_column: str = "Title", queries=(TITLES, COUNT)) -> Path:
    """A project: books.sqlite, and an nl2sql.yaml whose curated section is curated.yaml."""
    with sqlite3.connect(tmp_path / "books.sqlite") as conn:
        conn.execute(f"CREATE TABLE Book (BookId INTEGER PRIMARY KEY, {title_column} TEXT, Year INTEGER)")
        conn.executemany(f"INSERT INTO Book ({title_column}, Year) VALUES (?, ?)",
                         [("Dune", 2001), ("Emma", 2001), ("Ulysses", 2001), ("Beloved", 1999)])
    _curated(tmp_path, *queries)
    make_config(tmp_path, datasources=[{"id": "books", "type": "sqlite", "connection": {"database": "books.sqlite"}}],
                curated={"file": "curated.yaml"})
    return tmp_path


def _curated(project: Path, *queries) -> None:
    (project / "curated.yaml").write_text(yaml.safe_dump(
        {"datasources": {"books": {"verified_queries": list(queries)}}}), encoding="utf-8")


def _invoke(project: Path):
    result = runner.invoke(app, ["--config", str(project / "nl2sql.yaml"), "curated", "test"])
    return result, ANSI.sub("", result.output)


def _test(project: Path):
    with NL2SQL(load_config(project / "nl2sql.yaml")) as engine:
        return engine.curated.test()


def test_every_query_runs_with_its_defaults_and_examples(tmp_path):
    report = _test(_project(tmp_path))

    assert report.ok, report
    by_id = {e.query_id: e for e in report.entries}
    assert by_id["titles_by_year"].status == "PASS"
    # The example for year, the default for n: two of the three 2001 books.
    assert by_id["titles_by_year"].params == {"year": 2001, "n": 2}
    assert by_id["titles_by_year"].row_count == 2
    assert by_id["book_count"].status == "PASS"
    assert by_id["book_count"].row_count == 1


def test_a_renamed_column_fails_and_exits_one(tmp_path):
    project = _project(tmp_path, title_column="Name")

    result, out = _invoke(project)

    assert result.exit_code == 1, out
    assert "titles_by_year" in out
    assert "FAIL" in out
    assert "Title" in out  # the driver's own reason, naming the missing column
    # The other query still ran and passed.
    assert "book_count" in out
    assert "PASS" in out


def test_a_passing_file_exits_zero(tmp_path):
    result, out = _invoke(_project(tmp_path))

    assert result.exit_code == 0, out
    assert "titles_by_year" in out
    assert "year=2001" in out


def test_a_param_with_neither_default_nor_example_fails_without_running(tmp_path):
    no_value = dict(TITLES, params={"year": {"type": "int"}, "n": {"type": "int", "default": 2}})
    project = _project(tmp_path, queries=[no_value])

    report = _test(project)

    assert not report.ok
    [entry] = report.entries
    assert entry.status == "FAIL"
    assert "year" in entry.details and "example" in entry.details


def test_a_write_is_refused_by_the_startup_checks_and_never_runs(tmp_path):
    wipe = {"id": "wipe", "description": "No", "questions": ["Wipe"], "sql": "DELETE FROM Book"}
    project = _project(tmp_path, queries=[wipe])

    with pytest.raises(ConfigError, match="wipe"):
        _test(project)
    result, out = _invoke(project)

    assert result.exit_code == 1, out
    assert "read-only" in out
    with sqlite3.connect(project / "books.sqlite") as conn:
        assert conn.execute("SELECT COUNT(*) FROM Book").fetchone()[0] == 4


def test_a_missing_file_exits_one(tmp_path):
    project = _project(tmp_path)
    (project / "curated.yaml").unlink()

    result, out = _invoke(project)

    assert result.exit_code == 1, out
    assert "curated.yaml" in out


def test_without_a_curated_section_there_is_nothing_to_test(tmp_path):
    project = _project(tmp_path)
    make_config(project, datasources=[{"id": "books", "type": "sqlite", "connection": {"database": "books.sqlite"}}])

    result, out = _invoke(project)

    assert result.exit_code == 1, out
    assert "no curated section" in out
