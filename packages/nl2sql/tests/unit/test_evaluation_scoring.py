"""Row comparison, strict and lenient, as the ``result_match`` scorer reads it.

Key-free. Each scorer's verdict on a whole case run is ``test_eval_scorers.py``.
"""
from __future__ import annotations

from nl2sql.evaluation.evaluator import ModelEvaluator

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


# --- compare_results_lenient ------------------------------------------------
#
# The shapes come from the committed gpt-5.4 run
# (``benchmarks/tier2/chinook/2026-09-22_27cad30_gpt-5.4.json``): a person
# reading those rows would accept them, strict execution match does not.

lenient = ModelEvaluator.compare_results_lenient


def test_lenient_accepts_everything_strict_accepts():
    assert lenient([["USA", 13], ["Canada", 8]], GOLD, order_matters=True)
    assert lenient([["Canada", 8], ["USA", 13]], GOLD, order_matters=False)
    assert lenient([], [])


def test_lenient_accepts_a_reordered_column():
    # FLEX's false negative: the same two columns, the other way round.
    assert lenient([[13, "USA"], [8, "Canada"]], GOLD, order_matters=True)
    assert not compare([[13, "USA"], [8, "Canada"]], GOLD, order_matters=True)


def test_lenient_accepts_one_extra_column():
    # chinook_011-shaped: the answer carries a key the gold answer does not.
    rows = [["USA", 13, 1], ["Canada", 8, 2]]
    assert lenient(rows, GOLD, order_matters=True)
    assert not compare(rows, GOLD, order_matters=True)


def test_lenient_keeps_the_row_count_and_the_row_order_rule():
    assert not lenient([["USA", 13]], GOLD)
    assert not lenient([["Canada", 8], ["USA", 13]], GOLD, order_matters=True)


def test_lenient_normalises_a_year_label_against_a_date():
    # chinook_004/014/015: gold labels the year '2009', the model '2009-01-01'.
    gold = [{"year": "2009", "revenue": 449.46}, {"year": "2010", "revenue": 481.45}]
    rows = [["2009-01-01", 449.46], ["2010-01-01", 481.45]]
    assert lenient(rows, gold)
    assert not compare(rows, gold)


def test_lenient_normalises_a_month_label_against_a_date():
    # chinook_010/029: gold labels the month '2013-02', the model '2013-02-01'.
    gold = [{"month": "2013-02", "revenue": 13.86}, {"month": "2013-04", "revenue": 18.81}]
    assert lenient([["2013-02-01", 13.86], ["2013-04-01", 18.810000000000002]], gold)


def test_lenient_normalises_only_dates_and_periods():
    assert not lenient([["2009-01-01"]], [{"year": "2010"}])
    assert not lenient([["2009"]], [{"year": "2009-01-01"}])   # only gold may be the shorter label
    assert not lenient([["49.625"]], [{"total": "49.62"}])     # two strings still compare as strings
    assert not lenient([["2009-01-01"]], [{"n": 2009}])        # a number is not a period label


def test_lenient_caps_how_many_columns_a_result_may_carry():
    # At most twice the gold columns, or two more, whichever is larger: SELECT *
    # cannot pass by carrying the gold columns among many others.
    assert ModelEvaluator.lenient_column_cap(1) == 3
    assert ModelEvaluator.lenient_column_cap(2) == 4
    assert ModelEvaluator.lenient_column_cap(3) == 6
    wide = [["USA", 13, "x", "y", "z"], ["Canada", 8, "x", "y", "z"]]
    assert not lenient(wide, GOLD)
    assert lenient([["x", "USA", 13, "y"], ["x", "Canada", 8, "y"]], GOLD)


def test_lenient_refuses_a_constant_or_all_null_column_over_several_rows():
    # A hard-coded literal has the right height and tells the gold column
    # nothing, so it may not answer for one; nor may an all-null column.
    assert not lenient([["USA", 2013], ["Canada", 2013]], [{"y": 2013, "c": "USA"}, {"y": 2013, "c": "Canada"}])
    assert not lenient([[1, None], [2, None]], [{"a": None, "b": 1}, {"a": None, "b": 2}])
    # Strict still decides that shape, and lenient accepts everything strict does.
    assert lenient([[2013, "USA"], [2013, "Canada"]], [{"y": 2013, "c": "USA"}, {"y": 2013, "c": "Canada"}])
    # On one row every column is constant, so the rule is off there.
    assert lenient([[13, "USA"]], [{"c": "USA", "n": 13}])


def test_lenient_refuses_values_swapped_between_rows():
    # Each gold column is matched as a multiset, but the rows must still line up.
    assert not lenient([["USA", 8], ["Canada", 13]], GOLD)


def test_lenient_refuses_a_missing_gold_column():
    assert not lenient([["USA"], ["Canada"]], GOLD)


def test_lenient_still_needs_every_gold_column_matched():
    # chinook_008: the right genres, but the longest track's name is missing.
    gold = [{"genre": "Jazz", "track": "My Funny Valentine", "ms": 907520},
            {"genre": "Metal", "track": "Rime of the Ancient Mariner", "ms": 816509}]
    assert not lenient([["Jazz", 907520], ["Metal", 816509]], gold)
