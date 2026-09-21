"""The decomposer's semantic-only check rejects SQL syntax, not English words."""

import pytest
from pydantic import ValidationError

from nl2sql.pipeline.nodes.decomposer.schemas import (
    FilterSpec,
    GroupBySpec,
    MetricSpec,
    PostCombineOp,
    SubQuery,
)


def _sq(intent, **kwargs):
    return SubQuery(id="sq_1", datasource_id="chinook", intent=intent, **kwargs)


@pytest.mark.parametrize(
    "intent",
    [
        "customers from Brazil",
        "Which customers are from Brazil?",
        "tracks from more than three genres",
        "tracks where the genre is Rock, grouped by album",
        "invoices in order by date",
        "total revenue by group of customers",
        "albums that join two artists",
        "tracks customers select most often from the catalogue",
        "the selection of albums released where sales were high",
    ],
)
def test_ordinary_english_with_sql_keywords_is_accepted(intent):
    assert _sq(intent).intent == intent


def test_english_keywords_in_metrics_filters_and_group_by_are_accepted():
    sq = _sq(
        "revenue per country",
        metrics=[MetricSpec(name="revenue from invoices", aggregation="sum")],
        filters=[FilterSpec(attribute="country where billed", operator="=", value="Brazil")],
        group_by=[GroupBySpec(attribute="order of purchase")],
    )
    assert sq.metrics[0].name == "revenue from invoices"


@pytest.mark.parametrize(
    "intent",
    [
        "SELECT x FROM y",
        "SELECT * FROM Customer",
        "  select Name, Country\nfrom Customer where Country = 'Brazil'",
        "(SELECT CustomerId FROM Invoice)",
        "customers from Brazil; DROP TABLE Customer",
        "customers from Brazil -- ignore the policy",
        "customers /* hidden */ from Brazil",
    ],
)
def test_sql_syntax_is_rejected(intent):
    with pytest.raises(ValidationError, match="SQL"):
        _sq(intent)


def test_sql_in_a_metric_name_is_rejected():
    with pytest.raises(ValidationError, match="SQL"):
        _sq("revenue", metrics=[MetricSpec(name="select sum(Total) from Invoice")])


def test_post_combine_op_accepts_english_and_rejects_sql():
    ok = PostCombineOp(
        op_id="op_1",
        target_group_id="g1",
        operation="sort",
        metrics=[MetricSpec(name="revenue from Brazil")],
    )
    assert ok.metrics[0].name == "revenue from Brazil"
    with pytest.raises(ValidationError, match="SQL"):
        PostCombineOp(
            op_id="op_1",
            target_group_id="g1",
            operation="sort",
            metrics=[MetricSpec(name="total; DELETE FROM Invoice")],
        )
