"""``nl2sql curated test`` and the facade it calls, ``CuratedAPI.test()``.

Each verified query runs once against its database, with each param's default
or else its example bound, read-only through the datasource's adapter. A query
that no longer runs (a column renamed under it, say) fails, and the command
exits non-zero, so it can run in CI after a schema change.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from nl2sql import CuratedAPI
from nl2sql.cli.main import app

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


def _project(tmp_path: Path, title_column: str = "Title") -> Path:
    database = tmp_path / "books.sqlite"
    with sqlite3.connect(database) as conn:
        conn.execute(f"CREATE TABLE Book (BookId INTEGER PRIMARY KEY, {title_column} TEXT, Year INTEGER)")
        conn.executemany(f"INSERT INTO Book ({title_column}, Year) VALUES (?, ?)",
                         [("Dune", 2001), ("Emma", 2001), ("Ulysses", 2001), ("Beloved", 1999)])
    (tmp_path / "datasources.yaml").write_text(yaml.safe_dump({"version": 1, "datasources": [{
        "id": "books", "connection": {"type": "sqlite", "database": str(database)},
    }]}), encoding="utf-8")
    (tmp_path / "secrets.yaml").write_text("version: 1\nproviders: []\n", encoding="utf-8")
    (tmp_path / "curated.yaml").write_text(yaml.safe_dump(
        {"datasources": {"books": {"verified_queries": [TITLES, COUNT]}}}), encoding="utf-8")
    return tmp_path


def _invoke(project: Path):
    result = runner.invoke(app, ["curated", "test", "--curated", str(project / "curated.yaml"),
                                 "--config", str(project / "datasources.yaml"),
                                 "--secrets", str(project / "secrets.yaml")])
    return result, ANSI.sub("", result.output)


def _test(project: Path):
    return CuratedAPI().test(curated_path=project / "curated.yaml",
                             datasources_path=project / "datasources.yaml",
                             secrets_path=project / "secrets.yaml")


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
    project = _project(tmp_path)
    no_value = dict(TITLES, params={"year": {"type": "int"}, "n": {"type": "int", "default": 2}})
    (project / "curated.yaml").write_text(yaml.safe_dump(
        {"datasources": {"books": {"verified_queries": [no_value]}}}), encoding="utf-8")

    report = _test(project)

    assert not report.ok
    [entry] = report.entries
    assert entry.status == "FAIL"
    assert "year" in entry.details and "example" in entry.details


def test_a_write_is_refused_by_the_static_checks_and_never_runs(tmp_path):
    project = _project(tmp_path)
    wipe = {"id": "wipe", "description": "No", "questions": ["Wipe"], "sql": "DELETE FROM Book"}
    (project / "curated.yaml").write_text(yaml.safe_dump(
        {"datasources": {"books": {"verified_queries": [wipe]}}}), encoding="utf-8")

    report = _test(project)

    assert not report.ok
    assert report.entries[0].status == "FAIL"
    with sqlite3.connect(project / "books.sqlite") as conn:
        assert conn.execute("SELECT COUNT(*) FROM Book").fetchone()[0] == 4


def test_a_missing_file_exits_one(tmp_path):
    project = _project(tmp_path)
    (project / "curated.yaml").unlink()

    result, out = _invoke(project)

    assert result.exit_code == 1, out
    assert "curated.yaml" in out
