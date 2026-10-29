"""The Pagila table-recall suite names the tables its gold SQL reads, and only real Pagila tables and columns (C-W4)."""
from __future__ import annotations

import pytest
import sqlglot
from pagila_snapshot import pagila_snapshot
from sqlglot import exp

from nl2sql.evaluation.suite import SUITES_DIR, load_suite

SUITE = load_suite(SUITES_DIR / "pagila.yaml")
SNAPSHOT = pagila_snapshot()
COLUMNS = {f"{c.table.table_name}.{col}".casefold() for c in SNAPSHOT.contract.tables.values() for col in c.columns}


def _tables_read(sql: str) -> set:
    tree = sqlglot.parse_one(sql, read="postgres")
    ctes = {cte.alias for cte in tree.find_all(exp.CTE)}
    return {t.name for t in tree.find_all(exp.Table) if t.name not in ctes}


def test_the_suite_holds_the_cases_the_design_asks_for():
    ids = [case.id for case in SUITE.cases]

    assert len(ids) == len(set(ids)) >= 10
    assert SUITE.cases[0].question == "Top 5 films by revenue in the top category"
    assert set(SUITE.cases[0].tables) == {"film", "film_category", "category", "inventory", "rental", "payment"}
    assert all(case.datasources == ["pagila"] and case.status == "approved" for case in SUITE.cases)


@pytest.mark.parametrize("case", SUITE.cases, ids=lambda c: c.id)
def test_pagila_suite_reads_the_tables_it_names(case):
    assert set(case.tables) == _tables_read(case.sql), case.id
    assert {c.split(".")[0] for c in case.columns} <= set(case.tables), case.id
    assert all(c.casefold() in COLUMNS for c in case.columns), case.id


def test_every_case_carries_expected_rows():
    assert all(case.rows for case in SUITE.cases), [c.id for c in SUITE.cases if not c.rows]


def test_the_demo_does_not_copy_a_suite_for_a_database_it_does_not_have(tmp_path):
    from nl2sql.config.project import init_project

    init_project(tmp_path, demo=True)

    copied = {p.name for p in (tmp_path / "evals").iterdir()}
    assert "chinook.yaml" in copied and "pagila.yaml" not in copied


@pytest.mark.parametrize("case_id", ["pagila_002", "pagila_003"])
def test_a_person_in_one_full_name_column_is_an_accepted_answer(case_id):
    """A run wrote the name as one column (C-W6f); the same people, order and totals as the gold's two columns."""
    case = next(c for c in SUITE.cases if c.id == case_id)

    assert len(case.alt_sql) == len(case.alt_rows) == 1
    name, measure = case.alt_rows[0][0].keys()
    assert [(r[name], r[measure]) for r in case.alt_rows[0]] == [
        (f"{g['first_name']} {g['last_name']}", g[next(k for k in g if k not in ("first_name", "last_name"))])
        for g in case.rows]
