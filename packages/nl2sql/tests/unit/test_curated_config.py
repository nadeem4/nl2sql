"""The ``curated`` section: its models, and the demo's verified queries.

The checks here need no database and no index: the YAML parses, matches the
models, query ids are unique, question placeholders and declared params agree,
and defaults, examples, bounds and choices agree with the declared type.
"""
from __future__ import annotations

import copy
import pathlib

import pytest
import yaml
from pydantic import ValidationError

from nl2sql.config import Curated
from nl2sql.config.curated import ParamSpec

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]

TOP_CUSTOMERS = {
    "id": "top_customers_by_revenue",
    "description": "Customers ranked by invoiced revenue in a calendar year",
    "questions": [
        "Who are our top {n} customers by revenue in {year}?",
        "Best {n} customers in {year}",
    ],
    "params": {
        "n": {"type": "int", "default": 10, "min": 1, "max": 100},
        "year": {"type": "int", "min": 2000, "max": 2100, "example": 2012},
    },
    "sql": "SELECT CustomerId FROM Invoice WHERE BillingYear = :year LIMIT :n",
}


def _file(*queries, metrics=None) -> dict:
    ds = {"verified_queries": [copy.deepcopy(q) for q in queries]}
    if metrics is not None:
        ds["metrics"] = metrics
    return {"datasources": {"chinook": ds}}


def _query(**changes) -> dict:
    query = copy.deepcopy(TOP_CUSTOMERS)
    query.update(changes)
    return query


# --------------------------------------------------------------------------
# The models
# --------------------------------------------------------------------------

def test_the_design_example_parses():
    cfg = Curated.model_validate(_file(TOP_CUSTOMERS, metrics=[{
        "name": "revenue",
        "description": "Invoiced revenue, before refunds",
        "expression": "SUM(InvoiceLine.UnitPrice * InvoiceLine.Quantity)",
        "table": "InvoiceLine",
    }]))

    [query] = cfg.datasources["chinook"].verified_queries
    assert query.id == "top_customers_by_revenue"
    assert query.params["n"].default == 10
    assert query.params["year"].example == 2012
    assert cfg.datasources["chinook"].metrics[0].name == "revenue"


def test_the_demos_verified_queries_parse():
    from nl2sql.datasets.demo import DEMO_CURATED

    cfg = Curated.model_validate(DEMO_CURATED)

    assert cfg.datasources["chinook"].verified_queries


def test_an_empty_file_is_a_valid_file_with_nothing_in_it():
    assert Curated.model_validate({}).datasources == {}


@pytest.mark.parametrize("where, raw", [
    ("file", {"datasources": {}, "verifed_queries": []}),
    ("datasource", {"datasources": {"chinook": {"verifed_queries": []}}}),
    ("query", _file(_query(descripton="typo"))),
    ("param", _file(_query(params={"n": {"type": "int", "defualt": 3}, "year": {"type": "int"}}))),
])
def test_a_misspelt_key_is_an_error_not_ignored(where, raw):
    with pytest.raises(ValidationError):
        Curated.model_validate(raw)


def test_query_ids_are_unique_within_a_datasource():
    with pytest.raises(ValidationError, match="top_customers_by_revenue"):
        Curated.model_validate(_file(TOP_CUSTOMERS, TOP_CUSTOMERS))


def test_the_same_id_in_two_datasources_is_fine():
    raw = {"datasources": {"a": {"verified_queries": [TOP_CUSTOMERS]},
                           "b": {"verified_queries": [TOP_CUSTOMERS]}}}

    assert set(Curated.model_validate(raw).datasources) == {"a", "b"}


@pytest.mark.parametrize("bad_id", ["Top Customers", "top-customers!", "", "1st"])
def test_a_query_id_is_a_slug(bad_id):
    with pytest.raises(ValidationError):
        Curated.model_validate(_file(_query(id=bad_id)))


def test_a_query_needs_at_least_one_question():
    with pytest.raises(ValidationError):
        Curated.model_validate(_file(_query(questions=[])))


def test_a_question_placeholder_must_be_a_declared_param():
    query = _query(questions=["Top {n} customers in {year} for {country}"])

    with pytest.raises(ValidationError, match="country"):
        Curated.model_validate(_file(query))


def test_a_declared_param_must_appear_in_a_question():
    query = _query(questions=["Top {n} customers"])

    with pytest.raises(ValidationError, match="year"):
        Curated.model_validate(_file(query))


def test_a_param_used_in_only_one_question_is_enough():
    query = _query(questions=["Top {n} customers in {year}", "Best customers in {year}"])

    assert Curated.model_validate(_file(query))


# --------------------------------------------------------------------------
# Param specs: values agree with the declared type
# --------------------------------------------------------------------------

@pytest.mark.parametrize("spec", [
    {"type": "int", "default": 5, "min": 1, "max": 10},
    {"type": "float", "default": 0.5, "min": 0, "max": 1},
    {"type": "float", "default": 2},
    {"type": "string", "default": "Rock", "choices": ["Rock", "Jazz"]},
    {"type": "string", "example": "anything"},
    {"type": "date", "default": "2012-01-01", "min": "2009-01-01", "max": "2013-12-31"},
    {"type": "int"},
])
def test_valid_param_specs(spec):
    assert ParamSpec.model_validate(spec)


@pytest.mark.parametrize("spec, fragment", [
    ({"type": "decimal"}, "type"),
    ({"type": "int", "default": "ten"}, "default"),
    ({"type": "int", "default": 2.5}, "default"),
    ({"type": "int", "default": True}, "default"),
    ({"type": "float", "example": "x"}, "example"),
    ({"type": "string", "default": 3}, "default"),
    ({"type": "date", "default": "12/31/2012"}, "default"),
    ({"type": "int", "default": 0, "min": 1}, "min"),
    ({"type": "int", "example": 101, "max": 100}, "max"),
    ({"type": "int", "min": 10, "max": 1}, "min"),
    ({"type": "date", "default": "2008-01-01", "min": "2009-01-01"}, "min"),
    ({"type": "string", "min": 1}, "min"),
    ({"type": "int", "choices": ["a"]}, "choices"),
    ({"type": "string", "default": "Pop", "choices": ["Rock", "Jazz"]}, "choices"),
])
def test_invalid_param_specs(spec, fragment):
    with pytest.raises(ValidationError, match=fragment):
        ParamSpec.model_validate(spec)
