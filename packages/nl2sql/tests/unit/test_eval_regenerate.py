"""``regenerate``: a suite's rows come from its datasource's adapter, never from a driver of its own.

Key-free: the Chinook suite is regenerated through the sqlite adapter over
the vendored ``chinook.sqlite``.
"""
from __future__ import annotations

import yaml

from nl2sql_sdk import Limits

from nl2sql.adapters.sqlite.adapter import SqliteAdapter, SqliteConnection
from nl2sql.datasets import CHINOOK_DB_PATH
from nl2sql.evaluation.regenerate import regenerate
from nl2sql.evaluation.suite import CHINOOK_SUITE_PATH, load_suite
from nl2sql.testing.runtime import stub_ds_registry


class _Spy(SqliteAdapter):
    """The sqlite adapter, counting the SQL it is asked to run."""

    def __init__(self):
        super().__init__("chinook", SqliteConnection(database=CHINOOK_DB_PATH.as_posix()), Limits())
        self.ran = []

    def execute(self, request):
        self.ran.append(request.payload["sql"])
        return super().execute(request)


def _copy(tmp_path):
    copy = tmp_path / "chinook.yaml"
    copy.write_text(CHINOOK_SUITE_PATH.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
    return copy


def test_regenerate_runs_through_the_adapter(tmp_path):
    adapter = _Spy()
    copy = _copy(tmp_path)

    report = regenerate(stub_ds_registry(adapter), copy)

    suite = load_suite(CHINOOK_SUITE_PATH)
    every_sql = [s for c in suite.cases if c.sql for s in [c.sql, *c.alt_sql]]
    assert sorted(adapter.ran) == sorted(every_sql)
    assert report.written == len(every_sql) and report.failed == []
    # The committed suite is exactly what regenerate writes: no row was typed by hand.
    assert copy.read_text(encoding="utf-8") == CHINOOK_SUITE_PATH.read_text(encoding="utf-8")


def test_regenerate_leaves_a_failing_case_unchanged(tmp_path):
    copy = _copy(tmp_path)
    document = yaml.safe_load(copy.read_text(encoding="utf-8"))
    first = document["cases"][0]
    first["sql"] = "SELECT Nope FROM Customer\n"
    first["rows"] = [{"x": 1}]
    copy.write_text(yaml.safe_dump(document, sort_keys=False, allow_unicode=True), encoding="utf-8")

    report = regenerate(stub_ds_registry(_Spy()), copy)

    [failure] = report.failed
    assert failure["case"] == first["id"] and failure["error"]
    after = load_suite(copy).cases[0]
    assert after.sql == "SELECT Nope FROM Customer\n" and after.rows == [{"x": 1}]
    # Every other case was still written.
    assert load_suite(copy).cases[1].rows == load_suite(CHINOOK_SUITE_PATH).cases[1].rows


def test_regenerate_writes_only_approved_cases(tmp_path):
    path = tmp_path / "mine.yaml"
    path.write_text(yaml.safe_dump({"version": 1, "cases": [
        {"id": "d", "question": "How many customers?", "datasources": ["chinook"], "status": "draft",
         "outcome": {"admin": "allowed"}, "sql": "SELECT COUNT(*) AS n FROM Customer"},
        {"id": "a", "question": "How many artists?", "datasources": ["chinook"], "status": "approved",
         "outcome": {"admin": "allowed"}, "sql": "SELECT COUNT(*) AS n FROM Artist", "rows": []},
    ]}), encoding="utf-8")
    adapter = _Spy()

    regenerate(stub_ds_registry(adapter), path)

    draft, approved = load_suite(path).cases
    assert adapter.ran == ["SELECT COUNT(*) AS n FROM Artist\n"]
    assert draft.rows is None and approved.rows == [{"n": 275}]
