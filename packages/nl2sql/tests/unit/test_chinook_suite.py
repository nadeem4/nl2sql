"""The Chinook suite stays true to the database, the policy and the gold data it was moved from.

Key-free: every check runs against the vendored ``chinook.sqlite`` (through
the sqlite adapter, as ``regenerate`` does) and the demo's
``CHINOOK_POLICIES``, never an LLM.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict

import pytest
import sqlglot
from sqlglot import exp

from nl2sql.config import Policies, Role
from nl2sql.datasets import CHINOOK_DB_PATH
from nl2sql.datasets.chinook import CHINOOK_POLICIES, CHINOOK_QUESTIONS
from nl2sql.evaluation.suite import CHINOOK_SUITE_PATH, EvalCase, load_suite
from nl2sql.pipeline.nodes.ast_planner.schemas import PlanModel
from nl2sql.policies import PolicyEngine, UserContext

SUITE = load_suite(CHINOOK_SUITE_PATH)
CASES = SUITE.cases
ANSWERABLE = [c for c in CASES if c.sql is not None]
ROLES = set(CHINOOK_POLICIES)

# SHA-256 over every case's id, question, datasource, difficulty, tags,
# paraphrase group, outcomes, needed tables and columns, order rule, SQL, rows,
# alternatives and plan, computed from chinook_gold.yaml and
# chinook_gold_plans.yaml with the 0.6 loader before they were deleted
# (wave_a.md, section 8). The data never changes; only the keys around it did.
GOLD_DIGEST = "9287e40e4b019a1d8e42c48af2a141fda1e4fbf3077727fb4931cc6acecb42b7"


def _canonical(case: EvalCase) -> dict:
    return {"id": case.id, "question": case.question, "datasources": case.datasources,
            "difficulty": case.difficulty, "tags": case.tags, "paraphrase_group": case.paraphrase_group,
            "outcome": case.outcome, "tables": case.tables, "columns": case.columns,
            "order_matters": case.order_matters, "sql": case.sql, "rows": case.rows, "alt_sql": case.alt_sql,
            "alt_rows": case.alt_rows, "plan": case.plan}


def test_chinook_suite_holds_the_gold_data_unchanged():
    cases = sorted((_canonical(c) for c in CASES), key=lambda c: c["id"])
    digest = hashlib.sha256(json.dumps(cases, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    assert digest == GOLD_DIGEST, "The Chinook gold data changed. Add a reviewed alt_sql instead."


def test_every_moved_case_is_approved_and_hand_written():
    assert {(c.status, c.origin) for c in CASES} == {("approved", "hand")}
    assert {tuple(c.datasources) for c in CASES} == {("chinook",)}


def _schema() -> dict[str, set[str]]:
    con = sqlite3.connect(f"file:{CHINOOK_DB_PATH.as_posix()}?mode=ro", uri=True)
    try:
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        return {t: {r[1] for r in con.execute(f'PRAGMA table_info("{t}")')} for t in tables}
    finally:
        con.close()


SCHEMA = _schema()
POLICY = PolicyEngine(Policies(roles={name: Role(**role) for name, role in CHINOOK_POLICIES.items()}))


def _allowed(role: str, tables: list[str]) -> bool:
    scope = POLICY.scope(UserContext(roles=[role]), "chinook")
    return all(scope.allows(None, t) for t in tables)


def _sorted_rows(rows: list[dict]) -> list[tuple]:
    return sorted((tuple(sorted(r.items())) for r in rows), key=repr)


def test_about_forty_cases_with_unique_ids():
    assert 38 <= len(CASES) <= 50
    assert len({c.id for c in CASES}) == len(CASES)


def test_results_are_small_enough_to_read():
    for c in ANSWERABLE:
        assert c.rows is not None and len(c.rows) <= 25, c.id


def test_unanswerable_cases_have_no_sql_and_are_unanswerable_for_every_role():
    unanswerable = [c for c in CASES if "unanswerable" in c.tags]
    assert len(unanswerable) >= 3
    for c in unanswerable:
        assert c.sql is None and c.rows is None, c.id
        assert set(c.outcome.values()) == {"unanswerable"}, c.id
    for c in ANSWERABLE:
        assert "unanswerable" not in c.tags, c.id


def test_paraphrase_groups_share_one_result():
    groups: dict[str, list[EvalCase]] = defaultdict(list)
    for c in CASES:
        if c.paraphrase_group:
            groups[c.paraphrase_group].append(c)
    assert len(groups) >= 2
    for name, members in groups.items():
        assert len(members) >= 2, name
        assert all("paraphrase" in m.tags for m in members), name
        for m in members[1:]:
            assert m.order_matters == members[0].order_matters, name
            assert _sorted_rows(m.rows) == _sorted_rows(members[0].rows), name


def test_tables_and_columns_exist():
    for c in CASES:
        for t in c.tables:
            assert t in SCHEMA, f"{c.id}: unknown table {t}"
        for col in c.columns:
            table, _, column = col.partition(".")
            assert table in c.tables, f"{c.id}: {col} is outside tables"
            assert column in SCHEMA.get(table, set()), f"{c.id}: unknown column {col}"


@pytest.mark.parametrize("c", ANSWERABLE, ids=lambda c: c.id)
def test_tables_are_exactly_the_tables_in_the_sql(c: EvalCase):
    tree = sqlglot.parse_one(c.sql, read="sqlite")
    ctes = {cte.alias for cte in tree.find_all(exp.CTE)}
    assert {t.name for t in tree.find_all(exp.Table)} - ctes == set(c.tables)


def test_every_role_named_exists_in_the_demo_policy():
    for c in CASES:
        assert set(c.outcome) == ROLES, c.id


def test_the_outcome_follows_the_policy():
    for c in ANSWERABLE:
        for role, outcome in c.outcome.items():
            assert outcome == ("allowed" if _allowed(role, c.tables) else "refused"), f"{c.id}: {role}"


def test_rbac_denial_tag_marks_exactly_the_refused_cases():
    for c in CASES:
        assert ("refused" in c.outcome.values()) == ("rbac-denial" in c.tags), c.id


def test_every_restricted_role_is_refused_somewhere():
    restricted = {r for r, p in CHINOOK_POLICIES.items() if p["allow"] != ["*"] or p.get("deny")}
    for role in restricted:
        assert any(c.outcome[role] == "refused" for c in CASES), role


def test_the_demo_questions_are_included_verbatim():
    assert set(CHINOOK_QUESTIONS) <= {c.question for c in CASES}


# --- the plans, for mode plans -----------------------------------------------


def test_every_answerable_case_has_exactly_one_plan_and_no_other_does():
    assert {c.id for c in CASES if c.plan} == {c.id for c in ANSWERABLE}


@pytest.mark.parametrize("c", ANSWERABLE, ids=lambda c: c.id)
def test_each_plan_is_a_plan_model_reading_exactly_the_tables(c: EvalCase):
    plan = PlanModel.model_validate(c.plan)
    # The outcome is derived from the tables, so a plan reading any other
    # table would be scored against the wrong outcome.
    assert {t.name for t in plan.tables} == set(c.tables)
    assert all(s.alias for s in plan.select_items)
    if c.rows:
        assert len(plan.select_items) == len(c.rows[0])


# --- alternative answers -------------------------------------------------------

WITH_ALTERNATIVES = [c for c in CASES if c.alt_sql]


def test_some_cases_carry_a_reviewed_alternative_answer():
    assert WITH_ALTERNATIVES


@pytest.mark.parametrize("c", WITH_ALTERNATIVES, ids=lambda c: c.id)
def test_an_alternative_answers_from_the_same_tables_and_differs_from_rows(c: EvalCase):
    for sql, rows in zip(c.alt_sql, c.alt_rows):
        tree = sqlglot.parse_one(sql, read="sqlite")
        ctes = {cte.alias for cte in tree.find_all(exp.CTE)}
        assert {t.name for t in tree.find_all(exp.Table)} - ctes <= set(c.tables)
        assert 0 < len(rows) <= 25
        assert _sorted_rows(rows) != _sorted_rows(c.rows)


def test_answers_puts_rows_first_then_the_alternatives():
    c = WITH_ALTERNATIVES[0]
    assert c.answers() == [c.rows, *c.alt_rows]


def test_an_approved_alternative_without_its_rows_is_rejected():
    bad = WITH_ALTERNATIVES[0].model_dump()
    bad["alt_rows"] = []
    with pytest.raises(ValueError, match="alt_rows"):
        EvalCase.model_validate(bad)
    # A draft's rows are written later, by regenerate.
    EvalCase.model_validate({**bad, "status": "draft"})


def test_an_unanswerable_case_cannot_carry_an_alternative():
    bad = next(c for c in CASES if c.sql is None).model_dump()
    bad["alt_sql"], bad["alt_rows"] = ["SELECT 1"], [[{"x": 1}]]
    with pytest.raises(ValueError, match="unanswerable"):
        EvalCase.model_validate(bad)


def test_clarify_is_an_outcome_a_case_can_expect():
    case = EvalCase(id="c1", question="Show me the sales", outcome={"admin": "clarify"})
    assert case.outcome == {"admin": "clarify"}
    with pytest.raises(ValueError):
        EvalCase(id="c2", question="q", outcome={"admin": "maybe"})


def test_a_suite_refuses_repeated_ids():
    from nl2sql.evaluation.suite import EvalSuite

    case = {"id": "x", "question": "q", "outcome": {"admin": "allowed"}}
    with pytest.raises(ValueError, match="repeat"):
        EvalSuite.model_validate({"cases": [case, case]})
