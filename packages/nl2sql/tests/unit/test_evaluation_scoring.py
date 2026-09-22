"""Row comparison and per-role scoring for the Chinook gold evaluation.

Key-free: results are built by hand as ``QueryResult`` objects.
"""
from __future__ import annotations

from nl2sql.api.query_api import QueryResult, RowSample, SubQueryResult
from nl2sql.evaluation.evaluator import ModelEvaluator
from nl2sql.evaluation.gold import GoldQuestion
from nl2sql.pipeline.nodes.validator.node import REFUSAL_MESSAGE

compare = ModelEvaluator.compare_results

GOLD = [{"Country": "USA", "customer_count": 13}, {"Country": "Canada", "customer_count": 8}]


# --- compare_results --------------------------------------------------------


def test_column_names_and_aliases_are_ignored():
    assert compare([["USA", 13], ["Canada", 8]], GOLD, order_matters=True)
    assert compare([{"country": "USA", "n": 13}, {"country": "Canada", "n": 8}], GOLD, order_matters=True)


def test_column_order_is_kept():
    assert not compare([[13, "USA"], [8, "Canada"]], GOLD)


def test_row_order_only_counts_when_it_matters():
    rows = [["Canada", 8], ["USA", 13]]
    assert compare(rows, GOLD, order_matters=False)
    assert not compare(rows, GOLD, order_matters=True)


def test_numbers_match_within_two_decimal_rounding():
    gold = [{"total": 49.62}, {"avg": 5.37}]
    assert compare([[49.620000000000005], [5.3742857]], gold, order_matters=True)
    assert compare([[13.0]], [{"n": 13}])
    assert not compare([[5.38]], [{"avg": 5.37}])


def test_none_and_mixed_types_compare_without_raising():
    gold = [{"a": None, "b": 1}, {"a": "x", "b": 2.5}]
    assert compare([["x", 2.5], [None, 1]], gold, order_matters=False)
    assert not compare([["x", 2.5], [None, 2]], gold, order_matters=False)


def test_a_numeric_string_matches_the_number_it_spells():
    # SQLite's STRFTIME('%Y', ...) returns '2009' where another database returns 2009.
    assert compare([[2009]], [{"year": "2009"}])
    assert compare([["2009"]], [{"year": 2009}])
    assert compare([[" 49.62 "]], [{"total": 49.624}])
    assert not compare([["2010"]], [{"year": 2009}])


def test_non_numeric_strings_are_not_numbers():
    assert not compare([["2009-01"]], [{"month": 2009}])
    assert not compare([["nan"]], [{"x": float("nan")}])
    assert not compare([[True]], [{"x": "1"}])
    # Two strings still compare as strings.
    assert not compare([["2009"]], [{"year": "2009.0"}])


def test_row_and_column_counts_must_match():
    assert not compare([["USA", 13]], GOLD)
    assert not compare([["USA", 13, 1], ["Canada", 8, 1]], GOLD)
    assert compare([], [])


def test_duplicate_rows_are_matched_one_to_one():
    gold = [{"a": 1}, {"a": 1}, {"a": 2}]
    assert compare([[1], [2], [1]], gold)
    assert not compare([[1], [2], [2]], gold)


# --- score_case -------------------------------------------------------------


def _question(expected: dict, order_matters: bool = True, gold=GOLD) -> GoldQuestion:
    return GoldQuestion(
        id="q1", question="How many customers by country?", difficulty="easy", tags=[],
        needed_tables=["Customer"], needed_columns=[], expected=expected,
        order_matters=order_matters, gold_sql="SELECT 1", gold_result=gold,
    )


def _rows_result(rows, status="success") -> QueryResult:
    sample = RowSample(columns=["c", "n"], rows=rows, total_rows=len(rows))
    return QueryResult(status=status, sub_queries=[SubQueryResult(id="sq1", rows=sample, status="success")])


def _refusal(message=REFUSAL_MESSAGE) -> QueryResult:
    return QueryResult(status="error", errors=[{
        "node": "logical_validator", "message": message,
        "error_code": "SECURITY_VIOLATION", "severity": "CRITICAL"}])


def test_allowed_role_passes_on_the_gold_rows():
    q = _question({"admin": "allowed"})
    assert ModelEvaluator.score_case(q, "admin", _rows_result([["USA", 13], ["Canada", 8]])) == ("pass", "")


def test_allowed_role_fails_on_wrong_rows():
    q = _question({"admin": "allowed"})
    status, reason = ModelEvaluator.score_case(q, "admin", _rows_result([["USA", 14], ["Canada", 8]]))
    assert status == "fail" and "rows" in reason


def test_allowed_role_fails_on_an_error_with_its_message():
    q = _question({"admin": "allowed"})
    result = QueryResult(status="error", errors=[{"node": "generator", "message": "boom",
                                                  "error_code": "SQL_GEN_FAILED", "severity": "ERROR"}])
    status, reason = ModelEvaluator.score_case(q, "admin", result)
    assert status == "fail" and "SQL_GEN_FAILED" in reason and "boom" in reason


def test_allowed_role_uses_the_questions_order_matters():
    reversed_rows = _rows_result([["Canada", 8], ["USA", 13]])
    assert ModelEvaluator.score_case(_question({"a": "allowed"}, False), "a", reversed_rows)[0] == "pass"
    assert ModelEvaluator.score_case(_question({"a": "allowed"}, True), "a", reversed_rows)[0] == "fail"


def test_refused_role_passes_on_the_generic_refusal():
    q = _question({"viewer": "refused"})
    assert ModelEvaluator.score_case(q, "viewer", _refusal()) == ("pass", "")


def test_refused_role_fails_when_rows_come_back():
    q = _question({"viewer": "refused"})
    status, reason = ModelEvaluator.score_case(q, "viewer", _rows_result([["USA", 13], ["Canada", 8]]))
    assert status == "fail" and "refus" in reason


def test_refused_role_fails_when_the_refusal_names_the_table():
    q = _question({"viewer": "refused"})
    status, reason = ModelEvaluator.score_case(q, "viewer", _refusal("Role 'viewer' denied access to 'chinook.Customer'."))
    assert status == "fail" and "generic" in reason


def test_refused_role_fails_on_an_unrelated_error():
    q = _question({"viewer": "refused"})
    result = QueryResult(status="error", errors=[{"node": "generator", "message": "boom",
                                                  "error_code": "SQL_GEN_FAILED", "severity": "ERROR"}])
    assert ModelEvaluator.score_case(q, "viewer", result)[0] == "fail"


NOT_ANSWERABLE = QueryResult(status="error", errors=[{
    "node": "datasourceresolver", "error_code": "QUESTION_NOT_ANSWERABLE", "severity": "ERROR",
    "message": "This question can't be answered from the connected data (chinook)."}])


def test_unanswerable_passes_on_the_resolvers_refusal():
    q = _question({"admin": "unanswerable"}, gold=None)
    assert ModelEvaluator.score_case(q, "admin", NOT_ANSWERABLE) == ("pass", "")


def test_unanswerable_fails_when_the_question_runs():
    q = _question({"admin": "unanswerable"}, gold=None)
    status, reason = ModelEvaluator.score_case(q, "admin", _rows_result([["USA", 13]]))
    assert status == "fail" and "QUESTION_NOT_ANSWERABLE" in reason


def test_unanswerable_fails_on_another_error():
    q = _question({"viewer": "unanswerable"}, gold=None)
    status, reason = ModelEvaluator.score_case(q, "viewer", _refusal())
    assert status == "fail" and "SECURITY_VIOLATION" in reason


# --- summary ----------------------------------------------------------------


def test_summary_counts_outcomes_per_role():
    results = [
        {"role": "admin", "status": "pass"}, {"role": "admin", "status": "fail"},
        {"role": "viewer", "status": "pass"}, {"role": "viewer", "status": "skip"},
        {"role": "viewer", "status": "xfail"},
    ]
    summary = ModelEvaluator.summarize(results)
    assert summary["by_role"]["admin"] == {"pass": 1, "fail": 1, "skip": 0, "xfail": 0}
    assert summary["by_role"]["viewer"] == {"pass": 1, "fail": 0, "skip": 1, "xfail": 1}
    assert summary["total"] == {"pass": 2, "fail": 1, "skip": 1, "xfail": 1}
