"""Row comparison, strict and lenient, as the ``result_match`` scorer reads it.

Key-free. Each scorer's verdict on a whole case run is ``test_eval_scorers.py``.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from nl2sql.evaluation.evaluator import ModelEvaluator
from nl2sql.evaluation.suite import SUITES_DIR, load_suite

compare = ModelEvaluator.compare_results
lenient = ModelEvaluator.compare_results_lenient
extra_columns = ModelEvaluator.compare_results_extra_columns
reordered = ModelEvaluator.compare_results_reordered
UTC = dt.timezone.utc
PAGILA = {c.id: c for c in load_suite(SUITES_DIR / "pagila.yaml").cases}

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


# --- C-W6a: gold rows written as JSON against live Decimal and datetime cells ------


def test_a_decimal_matches_a_numeric_string_a_float_and_an_int():
    assert compare([[Decimal("3094.78")]], [["3094.78"]], order_matters=True)
    assert compare([[Decimal("3094.78")]], [[3094.78]], order_matters=True)
    assert compare([[Decimal("13")]], [[13]], order_matters=True)
    assert not compare([[Decimal("3094.78")]], [["3095.78"]], order_matters=True)


def test_a_datetime_matches_its_iso_string():
    when = dt.datetime(2022, 1, 1, tzinfo=UTC)
    assert compare([[when]], [["2022-01-01T00:00:00Z"]], order_matters=True)
    assert compare([[when]], [["2022-01-01T00:00:00+00:00"]], order_matters=True)
    assert not compare([[when]], [["2022-01-02T00:00:00Z"]], order_matters=True)
    # Two full datetimes are equal as instants.
    east = dt.datetime(2022, 1, 1, 5, tzinfo=dt.timezone(dt.timedelta(hours=5)))
    assert compare([[east]], [["2022-01-01T00:00:00Z"]], order_matters=True)


def test_a_date_only_label_matches_a_midnight_datetime_either_way():
    midnight = dt.datetime(2022, 1, 1, tzinfo=UTC)
    assert compare([["2022-01-01"]], [["2022-01-01T00:00:00Z"]], order_matters=True)
    assert compare([["2022-01-01T00:00:00Z"]], [["2022-01-01"]], order_matters=True)
    assert compare([[midnight]], [["2022-01-01"]], order_matters=True)
    assert compare([[dt.date(2022, 1, 1)]], [["2022-01-01T00:00:00Z"]], order_matters=True)
    assert not compare([["2022-01-01"]], [["2022-01-01T10:00:00Z"]], order_matters=True)


def test_a_date_only_label_is_midnight_in_the_other_values_own_zone():
    west = dt.timezone(dt.timedelta(hours=-5))
    assert not compare([[dt.datetime(2021, 12, 31, 19, tzinfo=west)]], [["2022-01-01"]], order_matters=True)
    assert compare([[dt.datetime(2022, 1, 1, tzinfo=west)]], [["2022-01-01"]], order_matters=True)
    assert compare([["2022-01-01T00:00:00-05:00"]], [["2022-01-01"]], order_matters=True)
    assert not compare([["2022-01-01"]], [["2021-12-31T19:00:00-05:00"]], order_matters=True)


def test_pagila_007_monthly_revenue_matches_its_gold():
    gold = PAGILA["pagila_007"].rows
    assert len(gold) == 7
    labels = [[row["month"][:10], Decimal(row["revenue"])] for row in gold]
    assert compare(labels, gold, order_matters=True)
    live = [[dt.datetime.fromisoformat(row["month"].replace("Z", "+00:00")), Decimal(row["revenue"])]
            for row in gold]
    assert compare(live, gold, order_matters=True)
    live[3][1] += 1
    assert not compare(live, gold, order_matters=True)


def test_a_date_object_meets_a_year_label_only_in_the_lenient_reading():
    assert lenient([[dt.date(2009, 1, 1), 5]], [["2009", 5]])
    assert not compare([[dt.date(2009, 1, 1), 5]], [["2009", 5]])


# --- the extra-column pass: the right rows, the gold's columns in order, strictly ----


def test_pagila_004_the_titles_with_an_extra_count_column_pass():
    gold = PAGILA["pagila_004"].rows
    got = [[row["title"], 30 - i % 5] for i, row in enumerate(gold)]
    assert extra_columns(got, gold, order_matters=True)
    assert extra_columns([[n, t] for t, n in got], gold, order_matters=True)   # the extra column first


def test_extra_columns_still_need_the_right_rows():
    gold = [{"c": "USA", "n": 13}, {"c": "Canada", "n": 8}]
    assert extra_columns([["USA", 13, "x"], ["Canada", 8, "y"]], gold, order_matters=True)
    assert not extra_columns([["USA", 13, "x"], ["Canada", 9, "y"]], gold, order_matters=True)
    assert not extra_columns([["USA", 13, "x"]], gold, order_matters=True)
    assert not extra_columns([["USA", 13], ["Canada", 8]], gold, order_matters=True)   # not wider


def test_extra_columns_may_come_in_any_order():
    gold = [{"c": "USA", "n": 13}, {"c": "Canada", "n": 8}]
    assert extra_columns([[13, "USA", "x"], [8, "Canada", "y"]], gold, order_matters=True)
    assert extra_columns([["x", 13, "USA"], ["y", 8, "Canada"]], gold, order_matters=True)
    assert not extra_columns([[13, "USA", "x"], [9, "Canada", "y"]], gold, order_matters=True)


def test_extra_columns_do_not_let_a_period_label_through():
    assert not extra_columns([["2009-01-01", 5, "a"]], [{"y": "2009", "n": 5}], order_matters=True)


def test_extra_columns_respect_the_column_cap():
    gold = [{"c": "USA"}, {"c": "Canada"}]
    assert extra_columns([["USA", 1, 2], ["Canada", 3, 4]], gold, order_matters=True)
    assert not extra_columns([["USA", 1, 2, 3], ["Canada", 3, 4, 5]], gold, order_matters=True)   # 4 > cap 3


# --- C-W7d: the same rows with the columns in another order pass ----


def test_reordered_columns_with_the_same_values_pass():
    gold = [{"title": "A", "category": "X", "revenue": 5}, {"title": "B", "category": "Y", "revenue": 7}]
    got = [["A", 5, "X"], ["B", 7, "Y"]]
    assert not compare(got, gold, order_matters=True)
    assert reordered(got, gold, order_matters=True)


def test_reordered_columns_fail_when_one_column_differs():
    gold = [{"title": "A", "category": "X", "revenue": 5}, {"title": "B", "category": "Y", "revenue": 7}]
    assert not reordered([["A", 5, "X"], ["B", 8, "Y"]], gold, order_matters=True)
    assert not reordered([["A", 5, "Z"], ["B", 7, "Y"]], gold, order_matters=True)


def test_two_numeric_columns_swapped_and_relabelled_pass_but_different_values_fail():
    gold = [{"a": 1, "b": 10}, {"a": 2, "b": 20}]
    assert reordered([[10, 1], [20, 2]], gold, order_matters=True)
    assert not reordered([[10, 1], [20, 3]], gold, order_matters=True)
    assert not reordered([[1, 10], [2, 21]], gold, order_matters=True)


def test_reordering_keeps_the_row_rules():
    gold = [{"a": "x", "b": 1}, {"a": "y", "b": 2}]
    swapped = [[2, "y"], [1, "x"]]
    assert not reordered(swapped, gold, order_matters=True)       # an ordered case still needs the order
    assert reordered(swapped, gold, order_matters=False)
    assert not reordered([[1, "x"]], gold, order_matters=False)   # the row count still counts
    assert not reordered([[2, "x"], [1, "y"]], gold, order_matters=False)   # values swapped between rows


def test_reordering_copes_with_duplicate_valued_columns():
    gold = [{"a": 1, "b": 1, "c": "x"}, {"a": 2, "b": 2, "c": "y"}]
    assert reordered([["x", 1, 1], ["y", 2, 2]], gold, order_matters=True)
    gold = [{"a": 1, "b": 1, "c": "x"}, {"a": 2, "b": 3, "c": "y"}]
    assert reordered([[1, "x", 1], [3, "y", 2]], gold, order_matters=True)
    assert not reordered([[1, "x", 1], [3, "y", 4]], gold, order_matters=True)


def test_reordering_uses_the_same_value_comparison():
    gold = [{"d": "2009-01-01", "n": Decimal("5.00")}]
    assert reordered([[5.004, dt.date(2009, 1, 1)]], gold, order_matters=True)


def test_reordering_needs_the_same_width_and_is_bounded():
    gold = [{"a": 1, "b": 2}]
    assert not reordered([[1, 2, 3]], gold, order_matters=True)   # wider is the extra-columns rule's
    wide = {f"c{i}": i for i in range(13)}
    assert not reordered([list(reversed(list(wide.values())))], [wide], order_matters=True)   # past the bound
    narrow = {f"c{i}": i for i in range(12)}
    assert reordered([list(reversed(list(narrow.values())))], [narrow], order_matters=True)


def test_column_picks_prefers_the_columns_in_their_own_order():
    pick = ModelEvaluator.column_picks
    gold = [{"a": 1, "b": 1}]
    assert pick([[1, 1, 9]], gold, order_matters=True) == (0, 1)
    assert pick([[9, 2]], gold, order_matters=True) is None
