"""The Chinook gold evaluation dataset stays true to the database and the policy.

Key-free: every check runs against the vendored ``chinook.sqlite`` and the
demo's ``CHINOOK_POLICIES``, never an LLM.
"""
from __future__ import annotations

import sqlite3
from collections import defaultdict

import pytest
import sqlglot
from sqlglot import exp

from nl2sql.cli.demo.chinook import CHINOOK_POLICIES, CHINOOK_QUESTIONS
from nl2sql.evaluation.gold import (
    CHINOOK_DB_PATH,
    GOLD_DATASET_PATH,
    GoldQuestion,
    execute_gold_sql,
    load_gold_dataset,
    regenerate,
)

DATASET = load_gold_dataset()
ANSWERABLE = [q for q in DATASET if q.gold_sql is not None]
ROLES = set(CHINOOK_POLICIES)


def _schema() -> dict[str, set[str]]:
    con = sqlite3.connect(f"file:{CHINOOK_DB_PATH.as_posix()}?mode=ro", uri=True)
    try:
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        return {t: {r[1] for r in con.execute(f'PRAGMA table_info("{t}")')} for t in tables}
    finally:
        con.close()


SCHEMA = _schema()


def _allowed(role: str, tables: list[str]) -> bool:
    allowed = CHINOOK_POLICIES[role]["allowed_tables"]
    return "*" in allowed or all(f"chinook.{t}" in allowed for t in tables)


def _sorted_rows(rows: list[dict]) -> list[tuple]:
    return sorted((tuple(sorted(r.items())) for r in rows), key=repr)


def test_every_entry_matches_the_model():
    assert DATASET, "dataset is empty"
    assert all(isinstance(q, GoldQuestion) for q in DATASET)


def test_about_forty_questions():
    assert 38 <= len(DATASET) <= 50


def test_ids_are_unique():
    ids = [q.id for q in DATASET]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("q", ANSWERABLE, ids=lambda q: q.id)
def test_gold_sql_reproduces_the_committed_result(q: GoldQuestion):
    actual = execute_gold_sql(q.gold_sql)
    if q.order_matters:
        assert actual == q.gold_result
    else:
        assert _sorted_rows(actual) == _sorted_rows(q.gold_result)


def test_committed_file_is_exactly_what_the_generator_writes(tmp_path):
    copy = tmp_path / "chinook_gold.yaml"
    copy.write_text(GOLD_DATASET_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    regenerate(copy)
    assert copy.read_text(encoding="utf-8") == GOLD_DATASET_PATH.read_text(encoding="utf-8")


def test_results_are_small_enough_to_read():
    for q in ANSWERABLE:
        assert q.gold_result is not None and len(q.gold_result) <= 25, q.id


def test_unanswerable_questions_have_no_sql_and_are_unanswerable_for_every_role():
    unanswerable = [q for q in DATASET if "unanswerable" in q.tags]
    assert len(unanswerable) >= 3
    for q in unanswerable:
        assert q.gold_sql is None and q.gold_result is None, q.id
        assert set(q.expected.values()) == {"unanswerable"}, q.id
    for q in ANSWERABLE:
        assert "unanswerable" not in q.tags, q.id


def test_paraphrase_groups_share_one_result():
    groups: dict[str, list[GoldQuestion]] = defaultdict(list)
    for q in DATASET:
        if q.paraphrase_group:
            groups[q.paraphrase_group].append(q)
    assert len(groups) >= 2
    for name, members in groups.items():
        assert len(members) >= 2, name
        assert all("paraphrase" in m.tags for m in members), name
        first = members[0]
        for m in members[1:]:
            assert m.order_matters == first.order_matters, name
            assert _sorted_rows(m.gold_result) == _sorted_rows(first.gold_result), name
    for q in DATASET:
        if "paraphrase" in q.tags:
            assert q.paraphrase_group, q.id


def test_needed_tables_and_columns_exist():
    for q in DATASET:
        for t in q.needed_tables:
            assert t in SCHEMA, f"{q.id}: unknown table {t}"
        for col in q.needed_columns:
            table, _, column = col.partition(".")
            assert table in q.needed_tables, f"{q.id}: {col} is outside needed_tables"
            assert column in SCHEMA.get(table, set()), f"{q.id}: unknown column {col}"


@pytest.mark.parametrize("q", ANSWERABLE, ids=lambda q: q.id)
def test_needed_tables_are_exactly_the_tables_in_gold_sql(q: GoldQuestion):
    tree = sqlglot.parse_one(q.gold_sql, read="sqlite")
    ctes = {c.alias for c in tree.find_all(exp.CTE)}
    tables = {t.name for t in tree.find_all(exp.Table)} - ctes
    assert tables == set(q.needed_tables)


def test_every_role_named_exists_in_the_demo_policy():
    for q in DATASET:
        assert set(q.expected) == ROLES, q.id


def test_expected_outcome_follows_the_policy():
    for q in ANSWERABLE:
        for role, outcome in q.expected.items():
            want = "allowed" if _allowed(role, q.needed_tables) else "refused"
            assert outcome == want, f"{q.id}: {role}"


def test_rbac_denial_tag_marks_exactly_the_refused_questions():
    for q in DATASET:
        refused = "refused" in q.expected.values()
        assert refused == ("rbac-denial" in q.tags), q.id


def test_every_restricted_role_is_refused_somewhere():
    restricted = {r for r, p in CHINOOK_POLICIES.items() if "*" not in p["allowed_tables"]}
    for role in restricted:
        assert any(q.expected[role] == "refused" for q in DATASET), role


def test_the_demo_questions_are_included_verbatim():
    questions = {q.question for q in DATASET}
    assert set(CHINOOK_QUESTIONS) <= questions


def test_questions_containing_from_and_where():
    words = [set(q.question.lower().replace("?", " ").split()) for q in DATASET]
    assert any("from" in w for w in words)
    assert any("where" in w for w in words)


# --- alternative gold answers -----------------------------------------------

WITH_ALTERNATIVES = [q for q in DATASET if q.alt_gold_sql]
ALTERNATIVES = [(q, i) for q in WITH_ALTERNATIVES for i in range(len(q.alt_gold_sql))]


def test_some_questions_carry_a_reviewed_alternative_answer():
    assert WITH_ALTERNATIVES, "no question has an alternative gold answer"


@pytest.mark.parametrize("q,i", ALTERNATIVES, ids=lambda v: v.id if isinstance(v, GoldQuestion) else str(v))
def test_every_alt_gold_sql_reproduces_its_committed_result(q: GoldQuestion, i: int):
    actual = execute_gold_sql(q.alt_gold_sql[i])
    expected = q.alt_gold_result[i]
    if q.order_matters:
        assert actual == expected
    else:
        assert _sorted_rows(actual) == _sorted_rows(expected)


@pytest.mark.parametrize("q,i", ALTERNATIVES, ids=lambda v: v.id if isinstance(v, GoldQuestion) else str(v))
def test_an_alternative_answers_the_same_question_from_the_same_tables(q: GoldQuestion, i: int):
    # An alternative is another reading or another shape of the same answer,
    # never a question needing data the gold answer does not.
    tree = sqlglot.parse_one(q.alt_gold_sql[i], read="sqlite")
    ctes = {c.alias for c in tree.find_all(exp.CTE)}
    assert {t.name for t in tree.find_all(exp.Table)} - ctes <= set(q.needed_tables), q.id


def test_alternative_results_are_small_enough_to_read_and_never_empty():
    for q in WITH_ALTERNATIVES:
        for i, rows in enumerate(q.alt_gold_result):
            assert 0 < len(rows) <= 25, f"{q.id}[{i}]"


def test_an_alternative_never_repeats_the_gold_answer():
    for q in WITH_ALTERNATIVES:
        for i, rows in enumerate(q.alt_gold_result):
            assert _sorted_rows(rows) != _sorted_rows(q.gold_result), f"{q.id}[{i}] is the gold answer again"


def test_answers_puts_the_gold_answer_first_then_the_alternatives():
    q = WITH_ALTERNATIVES[0]
    assert q.answers() == [q.gold_result, *q.alt_gold_result]
    plain = next(x for x in DATASET if not x.alt_gold_sql and x.gold_sql)
    assert plain.answers() == [plain.gold_result]


def test_an_alternative_without_its_generated_result_is_rejected():
    bad = WITH_ALTERNATIVES[0].model_dump()
    bad["alt_gold_result"] = None
    with pytest.raises(ValueError, match="alt_gold_result"):
        GoldQuestion.model_validate(bad)


def test_an_unanswerable_question_cannot_carry_an_alternative():
    bad = next(q for q in DATASET if q.gold_sql is None).model_dump()
    bad["alt_gold_sql"] = ["SELECT 1"]
    bad["alt_gold_result"] = [[{"x": 1}]]
    with pytest.raises(ValueError, match="unanswerable"):
        GoldQuestion.model_validate(bad)
