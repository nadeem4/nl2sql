"""The five built-in scorers: what each one passes, and the modes it scores.

Key-free: each scorer is given a hand-built ``CaseRun``.
"""
from __future__ import annotations

import pathlib
from decimal import Decimal

import pytest
import tomllib

from nl2sql.api.query_api import QueryResult, RowSample, SubQueryResult
from nl2sql.config.models import DEFAULT_SCORERS
from nl2sql.evaluation.scorers import CaseRun, Scorer, build_scorers, run_scorer
from nl2sql.evaluation.scorers.faithfulness import Faithfulness
from nl2sql.evaluation.scorers.outcome import Outcome
from nl2sql.evaluation.scorers.result_match import ResultMatch
from nl2sql.evaluation.scorers.retrieval_recall import RetrievalRecall
from nl2sql.evaluation.scorers.routing import Routing
from nl2sql.evaluation.suite import SUITES_DIR, EvalCase, load_suite
from nl2sql.pipeline.nodes.validator.node import REFUSAL_MESSAGE
from nl2sql.registry import Registry

PYPROJECT = pathlib.Path(__file__).resolve().parents[2] / "pyproject.toml"
GOLD = [{"Country": "USA", "customer_count": 13}, {"Country": "Canada", "customer_count": 8}]


def _case(outcome, *, rows=GOLD, order_matters=True, alt_rows=(), datasources=("chinook",)) -> EvalCase:
    return EvalCase(id="q1", question="How many customers by country?", datasources=list(datasources),
                    status="approved", outcome=outcome, tables=["Customer"],
                    columns=["Customer.Country", "Customer.CustomerId"], order_matters=order_matters,
                    sql="SELECT 1" if rows is not None else None, rows=rows,
                    alt_sql=["SELECT 2"] * len(alt_rows), alt_rows=list(alt_rows))


def _rows_result(rows, status="success", datasource="chinook", answer=None) -> QueryResult:
    sample = RowSample(columns=["c", "n"], rows=rows, total_rows=len(rows))
    return QueryResult(status=status, final_answer=answer,
                       sub_queries=[SubQueryResult(id="sq1", rows=sample, status="success",
                                                   datasource_id=datasource)])


def _run(case, role, result, mode="full", **extra) -> CaseRun:
    samples = [sq.rows for sq in result.sub_queries if sq.rows is not None]
    return CaseRun(case=case, role=role, mode=mode, result=result, sets=samples,
                   rows=samples[0].rows if samples else None, columns=samples[0].columns if samples else [],
                   **extra)


def _refusal(message=REFUSAL_MESSAGE) -> QueryResult:
    return QueryResult(status="error", errors=[{"node": "logical_validator", "message": message,
                                                "error_code": "SECURITY_VIOLATION", "severity": "CRITICAL"}])


NOT_ANSWERABLE = QueryResult(status="error", errors=[{
    "node": "datasourceresolver", "error_code": "QUESTION_NOT_ANSWERABLE", "severity": "ERROR",
    "message": "This question can't be answered from the connected data (chinook)."}])

result_match, outcome = ResultMatch(), Outcome()
PAGILA = {c.id: c for c in load_suite(SUITES_DIR / "pagila.yaml").cases}


# --- the registry ------------------------------------------------------------


def test_the_engine_declares_the_five_built_ins_as_entry_points():
    declared = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["entry-points"]["nl2sql.scorers"]
    assert set(declared) == set(DEFAULT_SCORERS)


def test_each_built_in_loads_through_the_registry_and_is_a_scorer():
    scorers = build_scorers(Registry.default(), list(DEFAULT_SCORERS))
    assert set(scorers) == set(DEFAULT_SCORERS)
    assert all(isinstance(s, Scorer) and type(s).interface_version == 1 for s in scorers.values())


@pytest.mark.parametrize("scorer,modes", [
    (ResultMatch, {"full", "plans"}), (Outcome, {"full", "plans"}), (RetrievalRecall, {"full", "retrieval"}),
    (Faithfulness, {"full"}), (Routing, {"full", "retrieval"})])
def test_each_scorer_scores_its_modes(scorer, modes):
    assert set(scorer.modes) == modes


def test_a_raising_scorer_marks_the_case_error():
    class Broken(ResultMatch):
        def score(self, run):
            raise RuntimeError("boom")

    score = run_scorer(Broken(), _run(_case({"admin": "allowed"}), "admin", _rows_result([["USA", 13]])))
    assert score.passed is None and score.value is None and "boom" in score.detail["error"]
    assert score.applies


# --- result_match ------------------------------------------------------------


def test_result_match_passes_on_the_rows_and_fails_on_wrong_ones():
    case = _case({"admin": "allowed"})
    good = result_match.score(_run(case, "admin", _rows_result([["USA", 13], ["Canada", 8]])))
    assert good.passed is True and good.value == 1.0
    bad = result_match.score(_run(case, "admin", _rows_result([["USA", 14], ["Canada", 8]])))
    assert bad.passed is False and bad.value == 0.0 and "rows" in bad.detail["reason"]


def test_result_match_reads_the_cases_order_rule():
    reversed_rows = _rows_result([["Canada", 8], ["USA", 13]])
    assert result_match.score(_run(_case({"a": "allowed"}, order_matters=False), "a", reversed_rows)).passed
    assert not result_match.score(_run(_case({"a": "allowed"}, order_matters=True), "a", reversed_rows)).passed


def test_result_match_does_not_apply_to_a_refusal_or_an_unanswerable_case():
    assert result_match.score(_run(_case({"v": "refused"}), "v", _refusal())).passed is None
    assert result_match.score(_run(_case({"a": "unanswerable"}, rows=None), "a", NOT_ANSWERABLE)).passed is None


def test_an_approved_answerable_case_without_sql_is_refused_unless_it_is_a_routing_case():
    with pytest.raises(ValueError, match="no sql.*kind: routing"):
        EvalCase(id="q9", question="?", datasources=["chinook"], status="approved", outcome={"admin": "allowed"})
    routing = EvalCase(id="q9", question="?", datasources=["chinook"], status="approved", kind="routing",
                       outcome={"admin": "allowed"})
    assert routing.kind == "routing"
    # A draft may still lack its SQL, and an unanswerable case never has one.
    EvalCase(id="d", question="?", status="draft", outcome={"admin": "allowed"})
    EvalCase(id="u", question="?", status="approved", outcome={"admin": "unanswerable"})
    with pytest.raises(ValueError, match="routing case.*sql"):
        EvalCase(id="r", question="?", datasources=["chinook"], kind="routing", sql="SELECT 1",
                 outcome={"admin": "allowed"})


def test_result_match_does_not_apply_to_a_routing_case():
    # A routing case says where the question goes, not what it returns.
    case = EvalCase(id="r", question="?", datasources=["chinook"], status="approved", kind="routing",
                    outcome={"admin": "allowed"})
    assert result_match.score(_run(case, "admin", _rows_result([["USA", 13]]))).passed is None


def test_result_match_fails_an_allowed_case_that_returned_no_rows():
    score = result_match.score(_run(_case({"a": "allowed"}), "a", QueryResult(status="error")))
    assert score.passed is False and "no rows" in score.detail["reason"]


def test_result_match_records_the_lenient_reading_beside_the_strict_one():
    # A period label against a date is lenient-only (C-W7d: a reordering now passes strictly enough to count).
    case = _case({"admin": "allowed"}, rows=[{"year": "2009", "n": 5}])
    score = result_match.score(_run(case, "admin", _rows_result([["2009-01-01", 5]])))
    assert score.passed is False and score.detail["lenient"] is True


def test_any_alternative_answer_passes():
    case = _case({"admin": "allowed"}, alt_rows=[[{"customer": "USA 13"}, {"customer": "Canada 8"}]])
    assert result_match.score(_run(case, "admin", _rows_result([["USA 13"], ["Canada 8"]]))).passed
    fail = result_match.score(_run(case, "admin", _rows_result([["nope"]])))
    assert not fail.passed and "1 alternative" in fail.detail["reason"]


# --- outcome -------------------------------------------------------------------


def test_allowed_passes_when_the_run_answered():
    assert outcome.score(_run(_case({"a": "allowed"}), "a", _rows_result([["USA", 13]]))).passed
    err = QueryResult(status="error", errors=[{"node": "generator", "message": "boom",
                                               "error_code": "SQL_GEN_FAILED", "severity": "ERROR"}])
    score = outcome.score(_run(_case({"a": "allowed"}), "a", err))
    assert score.passed is False and "SQL_GEN_FAILED" in score.detail["reason"]


def test_refused_passes_on_the_generic_refusal_only():
    case = _case({"viewer": "refused"})
    assert outcome.score(_run(case, "viewer", _refusal())).passed
    named = outcome.score(_run(case, "viewer", _refusal("Role 'viewer' denied access to 'chinook.Customer'.")))
    assert not named.passed and "generic" in named.detail["reason"]
    rows = outcome.score(_run(case, "viewer", _rows_result([["USA", 13]])))
    assert not rows.passed and "refus" in rows.detail["reason"]


def test_unanswerable_passes_on_the_resolvers_refusal_only():
    case = _case({"admin": "unanswerable"}, rows=None)
    assert outcome.score(_run(case, "admin", NOT_ANSWERABLE)).passed
    score = outcome.score(_run(case, "admin", _refusal()))
    assert not score.passed and "SECURITY_VIOLATION" in score.detail["reason"]


def test_clarify_passes_when_the_run_asked_for_clarification():
    case = _case({"admin": "clarify"}, rows=None)
    assert outcome.score(_run(case, "admin", QueryResult(status="needs_clarification"))).passed
    score = outcome.score(_run(case, "admin", _rows_result([["USA", 13]])))
    assert not score.passed and "needs_clarification" in score.detail["reason"]


# --- retrieval_recall ----------------------------------------------------------


def test_retrieval_recall_is_the_share_of_needed_tables_and_columns_sent():
    case = _case({"admin": "allowed"})
    run = _run(case, "admin", QueryResult(), mode="retrieval", retrieval={"customer": ["COUNTRY"]})
    score = RetrievalRecall().score(run)
    assert score.detail["table_recall"] == 1.0 and score.detail["column_recall"] == 0.5
    assert score.detail["missed_columns"] == ["Customer.CustomerId"]
    assert score.value == 0.5 and score.passed is False
    full = RetrievalRecall().score(_run(case, "admin", QueryResult(), mode="retrieval",
                                        retrieval={"Customer": ["Country", "CustomerId"]}))
    assert full.value == 1.0 and full.passed is True


def test_retrieval_recall_does_not_apply_without_a_retrieval_or_needed_tables():
    case = _case({"admin": "allowed"})
    assert RetrievalRecall().score(_run(case, "admin", QueryResult())).passed is None
    assert RetrievalRecall().score(_run(_case({"v": "refused"}), "v", QueryResult(), retrieval={})).passed is None


# --- faithfulness --------------------------------------------------------------


def test_faithfulness_checks_the_answer_against_the_rows():
    case = _case({"admin": "allowed"})
    answer = {"summary": "USA has 13 customers.", "content": ""}
    assert Faithfulness().score(_run(case, "admin", _rows_result([["USA", 13]], answer=answer))).passed
    wrong = {"summary": "USA has 14 customers.", "content": ""}
    score = Faithfulness().score(_run(case, "admin", _rows_result([["USA", 13]], answer=wrong)))
    assert score.passed is False and score.detail["unsupported_numbers"] == ["14"]


def test_faithfulness_does_not_apply_without_an_answer():
    assert Faithfulness().score(_run(_case({"a": "allowed"}), "a", _rows_result([["USA", 13]]))).passed is None


# --- routing -------------------------------------------------------------------


def test_routing_passes_when_the_run_used_exactly_the_cases_datasources():
    case = _case({"admin": "allowed"})
    assert Routing().score(_run(case, "admin", _rows_result([["USA", 13]]))).passed
    elsewhere = Routing().score(_run(case, "admin", _rows_result([["USA", 13]], datasource="support")))
    assert elsewhere.passed is False and elsewhere.detail["used"] == ["support"]


def test_routing_in_retrieval_mode_needs_every_datasource_among_the_ranked():
    case = _case({"admin": "allowed"}, datasources=("chinook", "support"))
    run = _run(case, "admin", QueryResult(), mode="retrieval", routed=["support", "chinook", "web"])
    assert Routing().score(run).passed
    missed = _run(case, "admin", QueryResult(), mode="retrieval", routed=["support", "web"])
    assert Routing().score(missed).passed is False


def test_routing_in_retrieval_mode_needs_the_top_ranked_datasource_to_be_one_of_the_cases():
    case = _case({"admin": "allowed"})
    second = Routing().score(_run(case, "admin", QueryResult(), mode="retrieval", routed=["support", "chinook"]))
    assert second.passed is False and second.value == 0.0
    assert second.detail["top1"] is False and second.detail["shortlist"] is True
    assert "ranked support first" in second.detail["reason"]
    first = Routing().score(_run(case, "admin", QueryResult(), mode="retrieval", routed=["chinook", "support"]))
    assert first.passed and first.detail["top1"] is True and first.detail["shortlist"] is True


def test_routing_in_retrieval_mode_with_nothing_ranked_misses_both():
    score = Routing().score(_run(_case({"admin": "allowed"}), "admin", QueryResult(), mode="retrieval", routed=[]))
    assert score.passed is False and score.detail["top1"] is False and score.detail["shortlist"] is False


def test_routing_does_not_apply_to_a_refused_role():
    assert Routing().score(_run(_case({"v": "refused"}), "v", _refusal())).passed is None


# --- C-W6a: extra columns pass; the preview size reaches the faithfulness check ----


def test_result_match_passes_the_right_rows_with_extra_columns():
    # pagila_004: the gold has the title only; the run grouped by title and added a count.
    case = PAGILA["pagila_004"]
    got = _rows_result([[row["title"], 30 - i % 5] for i, row in enumerate(case.rows)])
    score = result_match.score(_run(case, "admin", got))
    assert score.passed is True and score.value == 1.0
    assert score.detail["lenient"] is True and score.detail["match"] == "extra columns"


def test_result_match_names_a_strict_match_and_keeps_other_lenient_readings_failing():
    strict = result_match.score(_run(_case({"a": "allowed"}), "a", _rows_result([["USA", 13], ["Canada", 8]])))
    assert strict.passed and strict.detail["match"] == "strict"
    assert result_match.score(_run(_case({"a": "allowed"}), "a", _rows_result([["nope", 13], ["Canada", 8]]))).detail["match"] is None


def test_result_match_passes_reordered_columns_and_says_so():
    case = _case({"a": "allowed"})
    got = result_match.score(_run(case, "a", _rows_result([[13, "USA"], [8, "Canada"]])))
    assert got.passed and got.value == 1.0
    assert got.detail["match"] == "reordered" and got.detail["lenient"] is True
    wide = result_match.score(_run(case, "a", _rows_result([[13, "USA", "x"], [8, "Canada", "y"]])))
    assert wide.passed and wide.detail["match"] == "extra columns, reordered"
    wrong = result_match.score(_run(case, "a", _rows_result([[13, "USA"], [9, "Canada"]])))
    assert not wrong.passed and wrong.detail["match"] is None


def test_result_match_reordering_goes_through_the_alternatives_and_the_order_rule():
    case = _case({"a": "allowed"}, alt_rows=[[{"n": 1, "c": "x"}, {"n": 2, "c": "y"}]])
    assert result_match.score(_run(case, "a", _rows_result([["x", 1], ["y", 2]]))).detail["match"] == "reordered"
    ordered = _case({"a": "allowed"}, order_matters=True)
    assert not result_match.score(_run(ordered, "a", _rows_result([[8, "Canada"], [13, "USA"]]))).passed
    loose = _case({"a": "allowed"}, order_matters=False)
    assert result_match.score(_run(loose, "a", _rows_result([[8, "Canada"], [13, "USA"]]))).passed


def test_result_match_with_extra_columns_still_needs_the_right_rows():
    wrong = result_match.score(_run(_case({"a": "allowed"}), "a",
                                    _rows_result([["USA", 13, "x"], ["Canada", 9, "y"]])))
    assert not wrong.passed
    short = result_match.score(_run(_case({"a": "allowed"}), "a", _rows_result([["USA", 13, "x"]])))
    assert not short.passed


def test_result_match_pagila_005_with_decimal_counts_passes_strictly():
    case = PAGILA["pagila_005"]
    live = _rows_result([[row["name"], Decimal(row["rentals"])] for row in case.rows])
    assert result_match.score(_run(case, "admin", live)).detail["match"] == "strict"


def test_faithfulness_accepts_the_preview_size_the_run_was_given():
    answer = {"summary": "42 rows came back; the preview lists the first 20.", "content": ""}
    result = _rows_result([[f"T{i}", i * 100] for i in range(42)], answer=answer)
    case = _case({"admin": "allowed"})
    assert Faithfulness().score(_run(case, "admin", result, preview_rows=20)).passed
    assert not Faithfulness().score(_run(case, "admin", result)).passed


# --- C-W7a: a question is scored on its terminal result, not on the scans under it ----


def _scan(sid, rows):
    return SubQueryResult(id=sid, status="success", datasource_id="chinook",
                          rows=RowSample(columns=["c", "n"], rows=rows, total_rows=len(rows)))


def _terminal(rows):
    return RowSample(columns=["c", "n"], rows=rows, total_rows=len(rows))


def _split_run(case, terminals, scans, answer=None):
    result = QueryResult(status="success", final_answer=answer, sub_queries=scans)
    return CaseRun(case=case, role="admin", mode="full", result=result, sets=terminals,
                   rows=terminals[0].rows if terminals else None,
                   columns=terminals[0].columns if terminals else [])


def test_a_split_question_with_one_terminal_result_is_scored_on_that_result():
    case = _case({"admin": "allowed"}, rows=[{"c": "USA", "n": 21}])
    scans = [_scan("sq1", [["USA", 13]]), _scan("sq2", [["USA", 8]])]
    score = result_match.score(_split_run(case, [_terminal([["USA", 21]])], scans))
    assert score.passed is True and score.detail["match"] == "strict"


def test_a_split_question_with_a_wrong_terminal_result_fails_on_its_rows_not_on_the_count():
    case = _case({"admin": "allowed"}, rows=[{"c": "USA", "n": 21}])
    scans = [_scan("sq1", [["USA", 13]]), _scan("sq2", [["USA", 8]])]
    score = result_match.score(_split_run(case, [_terminal([["USA", 20]])], scans))
    assert score.passed is False and "one result set" not in score.detail["reason"]


def test_two_terminal_results_still_fail_as_two_result_sets():
    scans = [_scan("sq1", [["USA", 13]]), _scan("sq2", [["USA", 8]])]
    both = [_terminal([["USA", 13]]), _terminal([["USA", 8]])]
    score = result_match.score(_split_run(_case({"admin": "allowed"}), both, scans))
    assert score.passed is False and score.detail["reason"] == "expected one result set, got 2"


def test_a_single_query_case_scores_the_same_as_before():
    case = _case({"admin": "allowed"})
    rows = [["USA", 13], ["Canada", 8]]
    one = result_match.score(_run(case, "admin", _rows_result(rows)))
    terminal = result_match.score(_split_run(case, [_terminal(rows)], [_scan("sq1", rows)]))
    assert one == terminal and one.passed is True


def test_faithfulness_checks_the_answer_against_the_terminal_result():
    answer = {"summary": "The total is 21.", "content": ""}
    scans = [_scan("sq1", [["USA", 13]]), _scan("sq2", [["USA", 8]])]
    run = _split_run(_case({"admin": "allowed"}), [_terminal([["USA", 21]])], scans, answer=answer)
    assert Faithfulness().score(run).passed is True
