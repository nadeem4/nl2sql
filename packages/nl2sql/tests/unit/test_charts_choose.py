"""Auto charts (wave_c.md, section 5): five rules choose a chart from a result's column kinds, a template writes
its takeaway, and ``ChartSpec.to_vega_lite`` exports it with no colour. Pure functions: no engine, no model."""
from __future__ import annotations

import json

import polars as pl
import pytest

from nl2sql.charts.choose import choose
from nl2sql.config import ChartSettings
from nl2sql.execution.artifacts.results import column_kinds
from nl2sql.pipeline.nodes.ast_planner.schemas import Expr, GroupByItem, OrderItem, PlanModel, SelectItem

SETTINGS = ChartSettings()


def _draw(columns, kinds, rows, *, plan=None, kind=None, settings=SETTINGS, row_count=None):
    return choose(columns=columns, kinds=kinds, rows=rows, row_count=len(rows) if row_count is None else row_count,
                  title="Revenue", trace_id="t1", result_id="sq1", settings=settings, plan=plan, kind=kind)


def _col(name):
    return Expr(kind="column", alias="o", column_name=name)


def _lit(value):
    return Expr(kind="literal", value=value)


def _func(name, *args, aggregate=False):
    return Expr(kind="func", func_name=name, args=list(args), is_aggregate=aggregate)


MONTHS = [["2024-01-01", 100], ["2024-02-01", 150], ["2024-03-01", 120]]
CATEGORIES = [["books", 50], ["toys", 30], ["games", 20]]


# ---------------------------------------------------------------- column kinds (D112)

def test_column_kinds_follow_the_arrow_types():
    import datetime as dt
    from decimal import Decimal

    frame = pl.DataFrame({
        "i": [1], "f": [1.5], "d": [Decimal("1.25")], "s": ["x"], "b": [True],
        "day": [dt.date(2024, 1, 1)], "ts": [dt.datetime(2024, 1, 1, 12)], "t": [dt.time(1, 2)],
    })
    assert column_kinds(frame.schema) == {
        "i": "number", "f": "number", "d": "number", "s": "text", "b": "boolean",
        "day": "temporal", "ts": "temporal", "t": "temporal"}


def test_unknown_arrow_type_is_other():
    frame = pl.DataFrame({"blob": [b"x"], "items": [[1, 2]], "gap": [None]})
    assert set(column_kinds(frame.schema).values()) == {"other"}


# ---------------------------------------------------------------- the five rules

def test_one_row_one_measure_is_a_big_number():
    spec = _draw(["total"], {"total": "number"}, [[1234.5]])
    assert spec.kind == "big_number" and spec.value.field == "total" and spec.value.title == "Total"
    assert spec.takeaway == "Total: 1,234.5."
    assert spec.reason and spec.title == "Revenue"


def test_a_big_number_may_carry_one_label_column():
    spec = _draw(["region", "total"], {"region": "text", "total": "number"}, [["north", 7]])
    assert spec.kind == "big_number" and spec.value.field == "total"


def test_time_and_measure_is_a_line():
    spec = _draw(["month", "revenue"], {"month": "temporal", "revenue": "number"}, MONTHS)
    assert spec.kind == "line" and spec.x.field == "month" and [e.field for e in spec.y] == ["revenue"]
    assert spec.series is None


def test_a_line_is_sorted_by_x_ascending():
    rows = [MONTHS[2], MONTHS[0], MONTHS[1]]
    spec = _draw(["month", "revenue"], {"month": "temporal", "revenue": "number"}, rows)
    assert [r[0] for r in spec.data.rows] == ["2024-01-01", "2024-02-01", "2024-03-01"]


def test_a_line_may_carry_up_to_max_series_measures():
    kinds = {"month": "temporal", **{f"m{i}": "number" for i in range(6)}}
    cols = list(kinds)
    rows = [["2024-01-01", *range(6)], ["2024-02-01", *range(6)]]
    assert _draw(cols[:6], {k: kinds[k] for k in cols[:6]}, [r[:6] for r in rows]).kind == "line"
    assert _draw(cols, kinds, rows).kind == "table"


def test_a_line_needs_two_rows():
    spec = _draw(["month", "revenue"], {"month": "temporal", "revenue": "number"}, MONTHS[:1])
    assert spec.kind == "table"


def test_time_category_and_measure_is_a_line_with_series():
    rows = [["2024-01-01", "a", 1], ["2024-01-01", "b", 5], ["2024-02-01", "a", 3], ["2024-02-01", "b", 2]]
    spec = _draw(["month", "region", "revenue"], {"month": "temporal", "region": "text", "revenue": "number"}, rows)
    assert spec.kind == "line" and spec.series.field == "region"
    # The series with the largest last value is "a" (3).
    assert spec.takeaway.startswith("Revenue went from 1 in 2024-01-01 to 3 in 2024-02-01")


def test_too_many_series_is_a_table():
    rows = [["2024-01-01", f"r{i}", i] for i in range(6)]
    spec = _draw(["month", "region", "revenue"], {"month": "temporal", "region": "text", "revenue": "number"}, rows)
    assert spec.kind == "table"


def test_category_and_measure_is_a_bar():
    spec = _draw(["category", "sales"], {"category": "text", "sales": "number"}, CATEGORIES)
    assert spec.kind == "bar" and spec.x.field == "category" and [e.field for e in spec.y] == ["sales"]


def test_a_bar_is_ordered_by_the_first_measure_descending_without_a_plan_order():
    rows = [["toys", 30], ["books", 50], ["games", 20]]
    spec = _draw(["category", "sales"], {"category": "text", "sales": "number"}, rows)
    assert [r[0] for r in spec.data.rows] == ["books", "toys", "games"]


def test_a_bar_keeps_the_results_order_when_the_plan_ordered_it():
    rows = [["toys", 30], ["books", 50], ["games", 20]]
    plan = PlanModel(
        select_items=[SelectItem(expr=_col("category"), alias="category"),
                      SelectItem(expr=_func("SUM", _col("amt"), aggregate=True), alias="sales")],
        group_by=[GroupByItem(expr=_col("category"))],
        order_by=[OrderItem(expr=_col("category"), direction="asc")])
    spec = _draw(["category", "sales"], {"category": "text", "sales": "number"}, rows, plan=plan)
    assert [r[0] for r in spec.data.rows] == ["toys", "books", "games"]


def test_a_boolean_dimension_is_a_bar():
    spec = _draw(["active", "n"], {"active": "boolean", "n": "number"}, [[True, 3], [False, 1]])
    assert spec.kind == "bar"


def test_too_many_categories_is_a_table():
    rows = [[f"c{i}", i] for i in range(21)]
    assert _draw(["c", "n"], {"c": "text", "n": "number"}, rows).kind == "table"
    assert _draw(["c", "n"], {"c": "text", "n": "number"}, rows[:20]).kind == "bar"


def test_a_single_category_row_is_a_big_number_not_a_bar():
    assert _draw(["c", "n"], {"c": "text", "n": "number"}, [["a", 1]]).kind == "big_number"


def test_anything_else_is_a_table():
    spec = _draw(["a", "b"], {"a": "text", "b": "text"}, [["x", "y"], ["z", "w"]])
    assert spec.kind == "table" and spec.takeaway is None and spec.to_vega_lite() is None


def test_too_many_points_is_a_table_not_dropped_points():
    settings = ChartSettings(max_points=5)
    rows = [[f"2024-01-{d:02d}", d] for d in range(1, 6)]  # the 5 rows read
    spec = _draw(["day", "n"], {"day": "temporal", "n": "number"}, rows, settings=settings, row_count=9)
    assert spec.kind == "table" and spec.data.truncated is True and len(spec.data.rows) == 5
    # the same rows, all of them: a line
    whole = _draw(["day", "n"], {"day": "temporal", "n": "number"}, rows, settings=settings, row_count=5)
    assert whole.kind == "line" and whole.data.truncated is False


def test_forced_kind_the_data_cannot_draw_is_an_error():
    with pytest.raises(ValueError, match=r"line.*bar.*table|bar.*table"):
        _draw(["category", "sales"], {"category": "text", "sales": "number"}, CATEGORIES, kind="line")


def test_a_forced_kind_the_data_can_draw_is_used():
    spec = _draw(["category", "sales"], {"category": "text", "sales": "number"}, CATEGORIES, kind="table")
    assert spec.kind == "table"
    spec = _draw(["total"], {"total": "number"}, [[3]], kind="table")
    assert spec.kind == "table"


def test_an_unknown_forced_kind_is_an_error():
    with pytest.raises(ValueError, match="pie"):
        _draw(["total"], {"total": "number"}, [[3]], kind="pie")


# ---------------------------------------------------------------- plan refinements (D112)

def test_date_trunc_select_is_temporal():
    month = _func("DATE_TRUNC", _lit("month"), _col("created_at"))
    plan = PlanModel(
        select_items=[SelectItem(expr=month, alias="month"),
                      SelectItem(expr=_func("SUM", _col("total"), aggregate=True), alias="revenue")],
        group_by=[GroupByItem(expr=month)])
    # DATE_TRUNC returns an ISO string, so the file says text.
    spec = _draw(["month", "revenue"], {"month": "text", "revenue": "number"}, MONTHS, plan=plan)
    assert spec.kind == "line" and spec.x.kind == "temporal"


def test_a_grouped_year_part_is_a_year_axis():
    year = _func("DATE_PART", _lit("year"), _col("created_at"))
    plan = PlanModel(
        select_items=[SelectItem(expr=year, alias="yr"),
                      SelectItem(expr=_func("COUNT", _col("id"), aggregate=True), alias="n")],
        group_by=[GroupByItem(expr=year)])
    spec = _draw(["yr", "n"], {"yr": "number", "n": "number"}, [[2023, 4], [2024, 9]], plan=plan)
    assert spec.kind == "line" and spec.x.kind == "temporal"
    assert spec.to_vega_lite()["encoding"]["x"]["type"] == "ordinal"


def test_with_a_plan_a_grouped_number_is_a_dimension_not_a_measure():
    month = _func("DATE_PART", _lit("month"), _col("created_at"))
    plan = PlanModel(
        select_items=[SelectItem(expr=month, alias="m"),
                      SelectItem(expr=_func("SUM", _col("total"), aggregate=True), alias="revenue")],
        group_by=[GroupByItem(expr=month)])
    spec = _draw(["m", "revenue"], {"m": "number", "revenue": "number"}, [[1, 5], [2, 6]], plan=plan)
    assert spec.kind == "table"  # a month number is a dimension, but not a temporal or text one
    without = _draw(["m", "revenue"], {"m": "number", "revenue": "number"}, [[1, 5], [2, 6]])
    assert without.kind == "table"  # no plan: both numbers are measures, and there is no dimension


# ---------------------------------------------------------------- takeaways

def test_takeaway_line_change_and_bar_share():
    line = _draw(["month", "revenue"], {"month": "temporal", "revenue": "number"},
                 [["2024-01-01", 100], ["2024-03-01", 150]])
    assert line.takeaway == "Revenue went from 100 in 2024-01-01 to 150 in 2024-03-01 (+50%)."
    plan = PlanModel(
        select_items=[SelectItem(expr=_col("category"), alias="category"),
                      SelectItem(expr=_func("SUM", _col("amt"), aggregate=True), alias="sales")],
        group_by=[GroupByItem(expr=_col("category"))])
    bar = _draw(["category", "sales"], {"category": "text", "sales": "number"}, CATEGORIES, plan=plan)
    assert bar.takeaway == "Books has the highest sales: 50, 50% of the total shown."
    # Without a plan the measure is not known to be a sum or a count: no share.
    plain = _draw(["category", "sales"], {"category": "text", "sales": "number"}, CATEGORIES)
    assert plain.takeaway == "Books has the highest sales: 50."


def test_takeaway_numbers_have_separators_and_at_most_three_decimals():
    spec = _draw(["total"], {"total": "number"}, [[1234567.891234]])
    assert spec.takeaway == "Total: 1,234,567.891."


def test_a_null_end_gives_no_change_and_a_zero_start_gives_no_change():
    nulls = _draw(["month", "revenue"], {"month": "temporal", "revenue": "number"},
                  [["2024-01-01", None], ["2024-02-01", 5]])
    assert "%" not in nulls.takeaway
    zero = _draw(["month", "revenue"], {"month": "temporal", "revenue": "number"},
                 [["2024-01-01", 0], ["2024-02-01", 5]])
    assert "%" not in zero.takeaway


def test_a_bar_with_a_negative_value_has_no_share():
    plan = PlanModel(
        select_items=[SelectItem(expr=_col("category"), alias="category"),
                      SelectItem(expr=_func("SUM", _col("amt"), aggregate=True), alias="sales")],
        group_by=[GroupByItem(expr=_col("category"))])
    spec = _draw(["category", "sales"], {"category": "text", "sales": "number"},
                 [["a", 10], ["b", -5]], plan=plan)
    assert spec.takeaway == "A has the highest sales: 10."


# ---------------------------------------------------------------- Vega-Lite

def test_vega_lite_shape_per_kind():
    line = _draw(["month", "revenue"], {"month": "temporal", "revenue": "number"}, MONTHS).to_vega_lite()
    assert line["$schema"].startswith("https://vega.github.io/schema/vega-lite/v5")
    assert line["mark"] in ("line", {"type": "line"}) or line["mark"]["type"] == "line"
    assert line["data"]["values"] == [{"month": m, "revenue": v} for m, v in MONTHS]
    assert line["encoding"]["x"] == {"field": "month", "type": "temporal", "title": "Month"}
    assert line["encoding"]["y"]["type"] == "quantitative" and "color" not in line["encoding"]

    bar = _draw(["category", "sales"], {"category": "text", "sales": "number"}, CATEGORIES).to_vega_lite()
    assert bar["mark"] == "bar" or bar["mark"]["type"] == "bar"
    assert bar["encoding"]["x"]["type"] == "nominal" and bar["encoding"]["x"]["sort"] is None

    number = _draw(["total"], {"total": "number"}, [[5]]).to_vega_lite()
    assert number["mark"]["type"] == "text" and number["encoding"]["text"]["field"] == "total"

    several = _draw(["month", "a", "b"], {"month": "temporal", "a": "number", "b": "number"},
                    [["2024-01-01", 1, 2], ["2024-02-01", 3, 4]]).to_vega_lite()
    assert several["transform"] == [{"fold": ["a", "b"], "as": ["measure", "value"]}]
    assert several["encoding"]["y"]["field"] == "value" and several["encoding"]["color"]["field"] == "measure"

    series = _draw(["month", "region", "n"], {"month": "temporal", "region": "text", "n": "number"},
                   [["2024-01-01", "a", 1], ["2024-02-01", "a", 2]]).to_vega_lite()
    assert series["encoding"]["color"] == {"field": "region", "type": "nominal", "title": "Region"}


def test_vega_lite_sets_no_colour():
    specs = [
        _draw(["month", "revenue"], {"month": "temporal", "revenue": "number"}, MONTHS),
        _draw(["category", "sales"], {"category": "text", "sales": "number"}, CATEGORIES),
        _draw(["total"], {"total": "number"}, [[5]]),
        _draw(["month", "a", "b"], {"month": "temporal", "a": "number", "b": "number"},
              [["2024-01-01", 1, 2], ["2024-02-01", 3, 4]]),
    ]
    for spec in specs:
        text = json.dumps(spec.to_vega_lite())
        for forbidden in ('"scheme"', '"range"', '"config"', '"fill"', '"stroke"', '"background"', '"font'):
            assert forbidden not in text, forbidden
        color = spec.to_vega_lite()["encoding"].get("color")
        assert color is None or set(color) <= {"field", "type", "title"}  # a field, never a value
