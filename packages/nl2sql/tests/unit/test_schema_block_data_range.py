"""The schema block tells the planner what range of data each column holds.

Without it, "this year" on a database whose invoices stop in 2013 planned a
filter on the current year and returned nothing, confidently. The block now
carries ``range: [min, max]`` for date, time and numeric columns, including a
date kept as TEXT (SQLite has no date type, so the demo's support and web
analytics timestamps are TEXT), and the planner prompt says what to do when a
relative period falls outside it.
"""
import json

from nl2sql.pipeline.nodes.ast_planner.prompts import PLANNER_SYSTEM_PROMPT
from nl2sql.pipeline.nodes.schema_retriever.schema import Column, Table, render_schema_for_prompt


def _stats(lo, hi, samples=()):
    return {"null_percentage": 0.0, "distinct_count": 10, "min_value": lo, "max_value": hi,
            "sample_values": list(samples)}


def _columns(*columns, **table):
    row = json.loads(render_schema_for_prompt([Table(name="T", columns=list(columns), **table)]))
    return {c["name"]: c for c in row["columns"]}


def test_a_date_column_carries_its_range():
    cols = _columns(Column(name="InvoiceDate", type="DATETIME",
                           stats=_stats("2009-01-01 00:00:00", "2013-12-22 00:00:00")))

    assert cols["InvoiceDate"]["range"] == ["2009-01-01 00:00:00", "2013-12-22 00:00:00"]


def test_a_numeric_column_carries_its_range():
    cols = _columns(Column(name="Total", type="NUMERIC(10, 2)", stats=_stats(0.99, 25.86)))

    assert cols["Total"]["range"] == [0.99, 25.86]


def test_a_date_stored_as_text_carries_its_range_instead_of_samples():
    cols = _columns(Column(name="opened_at", type="TEXT", stats=_stats(
        "2013-01-02 09:00:00", "2013-12-30 17:45:00", ["2013-03-04 10:00:00"])))

    assert cols["opened_at"]["range"] == ["2013-01-02 09:00:00", "2013-12-30 17:45:00"]
    assert "sample_values" not in cols["opened_at"]


def test_an_ordinary_text_column_keeps_samples_and_gets_no_range():
    cols = _columns(Column(name="Country", type="NVARCHAR(40)", stats=_stats("Argentina", "USA", ["USA"])))

    assert "range" not in cols["Country"]
    assert cols["Country"]["sample_values"] == ["USA"]


def test_keys_get_no_range():
    """An id's min and max say nothing about the data and cost tokens on every table."""
    cols = _columns(
        Column(name="InvoiceId", type="INTEGER", stats=_stats(1, 412)),
        Column(name="CustomerId", type="INTEGER", stats=_stats(1, 59)),
        primary_key=["InvoiceId"],
        relationships=[{"from_table": "T", "to_table": "Customer", "from_columns": ["CustomerId"],
                        "to_columns": ["CustomerId"]}],
    )

    assert "range" not in cols["InvoiceId"] and "range" not in cols["CustomerId"]


def test_no_statistics_means_no_range():
    """A table the role cannot read arrives with its statistics stripped."""
    cols = _columns(Column(name="InvoiceDate", type="DATETIME", stats={}))

    assert cols["InvoiceDate"] == {"name": "InvoiceDate", "type": "DATETIME"}


def test_the_planner_is_told_what_to_do_outside_the_range():
    assert "range" in PLANNER_SYSTEM_PROMPT
    assert "outside" in PLANNER_SYSTEM_PROMPT and "latest" in PLANNER_SYSTEM_PROMPT
