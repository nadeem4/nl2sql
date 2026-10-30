"""A split answer's columns follow the question (C-W7c).

Pagila 001 ("Top 5 films by revenue in the top category"): the decomposer's one
sort op carried an ``expected_schema`` of exactly the answer's columns, but the
engine read it only for a ``project`` op, so the answer kept the combine order.
Now every op ends in its ``expected_schema``, after its filters, sort and limit,
and a join or compare group must carry one (the decomposer asks again if not).
"""
from __future__ import annotations

import json
from pathlib import Path

import polars as pl
import pytest

from nl2sql.aggregation.aggregator import AggregationService
from nl2sql.aggregation.engines.polars_duckdb import PolarsDuckdbEngine
from nl2sql.pipeline.nodes.decomposer.dag import build_execution_dag
from nl2sql.pipeline.nodes.decomposer.node import MAX_RETRIES, post_combine_problems
from nl2sql.pipeline.nodes.decomposer.schemas import DecomposerResponse
from nl2sql.pipeline.nodes.decomposer.prompts import DECOMPOSER_RETRY_FEEDBACK, DECOMPOSER_SYSTEM_PROMPT

# --- the engine: the schema is the final selection --------------------------------


@pytest.fixture
def engine():
    return PolarsDuckdbEngine.__new__(PolarsDuckdbEngine)  # no artifact store needed


FILMS = pl.DataFrame({"category": ["a", "a", "a"], "film": ["x", "y", "z"], "revenue": [1.0, 3.0, 2.0]})


def _schema(*names):
    return [{"name": n, "dtype": "string"} for n in names]


def test_a_sort_op_ends_in_its_expected_schema_in_that_order(engine):
    top = engine.post_op("sort", FILMS, {"order_by": [{"attribute": "revenue", "direction": "desc"}],
                                         "limit": 2, "expected_schema": _schema("revenue", "film")})

    assert top.columns == ["revenue", "film"]
    assert top.rows() == [(3.0, "y"), (2.0, "z")]


def test_a_sort_may_use_a_column_the_schema_drops(engine):
    top = engine.post_op("sort", FILMS, {"order_by": [{"attribute": "revenue", "direction": "desc"}],
                                         "expected_schema": _schema("film")})

    assert top.rows() == [("y",), ("z",), ("x",)]


def test_a_filter_may_use_a_column_the_schema_drops(engine):
    kept = engine.post_op("filter", FILMS, {"filters": [{"attribute": "revenue", "operator": ">", "value": 1.5}],
                                            "expected_schema": _schema("film")})

    assert sorted(kept.rows()) == [("y",), ("z",)]


def test_a_project_followed_by_a_sort_on_a_dropped_column_works(engine):
    # Used to pass validation and fail at collect: the select ran before the sort.
    projected = engine.post_op("project", FILMS, {"expected_schema": _schema("film"),
                                                  "order_by": [{"attribute": "revenue", "direction": "asc"}]})

    assert projected.rows() == [("x",), ("z",), ("y",)]


def test_an_aggregate_op_selects_among_its_own_outputs(engine):
    out = engine.post_op("aggregate", FILMS, {"group_by": [{"attribute": "category"}],
                                              "metrics": [{"name": "revenue", "aggregation": "sum"}],
                                              "expected_schema": _schema("revenue")})

    assert out.rows() == [(6.0,)]


def test_a_schema_name_the_op_cannot_produce_is_refused_with_what_is_available(engine):
    with pytest.raises(ValueError) as caught:
        engine.post_op("sort", FILMS, {"expected_schema": _schema("title")})

    assert "'title'" in str(caught.value) and "film" in str(caught.value)


def test_an_op_without_a_schema_keeps_every_column(engine):
    assert engine.post_op("sort", FILMS, {"order_by": [{"attribute": "revenue"}]}).columns == FILMS.columns


# --- validation before generation ---------------------------------------------------


def _sq(sq_id, *names):
    return {"id": sq_id, "datasource_id": "ds", "intent": sq_id,
            "expected_schema": [{"name": n, "dtype": "string"} for n in names]}


def _response(operation="join", ops=None, left=("category", "category_revenue"),
              right=("category", "film", "film_revenue"), keys=(("category", "category"),)):
    return DecomposerResponse.model_validate({
        "sub_queries": [_sq("sq_a", *left), _sq("sq_b", *right)],
        "combine_groups": [{"group_id": "g1", "operation": operation,
                            "inputs": [{"subquery_id": "sq_a", "role": "left"},
                                       {"subquery_id": "sq_b", "role": "right"}],
                            "join_keys": [{"left": a, "right": b} for a, b in keys]}],
        "post_combine_ops": ops if ops is not None else [_op()],
        "unmapped_subqueries": [],
    })


def _op(op_id="op1", operation="sort", schema=("film", "film_revenue"), **extra):
    return {"op_id": op_id, "target_group_id": "g1", "operation": operation,
            "expected_schema": [{"name": n, "dtype": "string"} for n in schema], **extra}


def test_a_schema_that_lists_the_columns_the_op_can_produce_passes():
    assert post_combine_problems(_response()) == []


def test_a_schema_name_the_combine_does_not_produce_is_rejected():
    [problem] = post_combine_problems(_response(ops=[_op(schema=("film", "title"))]))

    assert "'title'" in problem and "Available columns: " in problem and "film_revenue" in problem


def test_an_aggregates_schema_may_name_its_metric_outputs():
    op = _op(operation="aggregate", schema=("category", "film_revenue"),
             group_by=[{"attribute": "category"}], metrics=[{"name": "film_revenue", "aggregation": "sum"}])

    assert post_combine_problems(_response(ops=[op])) == []


def test_an_aggregates_schema_cannot_name_a_column_it_summed_away():
    op = _op(operation="aggregate", schema=("film",),
             group_by=[{"attribute": "category"}], metrics=[{"name": "film_revenue", "aggregation": "sum"}])

    [problem] = post_combine_problems(_response(ops=[op]))

    assert "'film'" in problem


@pytest.mark.parametrize("operation, keys", [("join", (("category", "category"),)),
                                              ("compare", (("category", "category"),))])
def test_a_join_or_compare_group_without_a_schema_on_its_op_is_rejected(operation, keys):
    [problem] = post_combine_problems(_response(operation=operation, keys=keys, ops=[_op(schema=())]))

    assert "no expected_schema" in problem and "in the order the question asks" in problem
    assert "helper columns" in problem and "_right" in problem


def test_two_ops_on_a_join_group_are_rejected_in_favour_of_one():
    ops = [_op("op1"), _op("op2", operation="limit", limit=5)]

    [problem] = post_combine_problems(_response(ops=ops))

    assert "ONE" in problem and "siblings" in problem and "expected_schema" in problem


def test_two_ops_on_a_compare_group_are_rejected():
    ops = [_op("op1"), _op("op2", operation="limit", limit=5)]

    assert len(post_combine_problems(_response(operation="compare", ops=ops))) == 1


def test_a_union_needs_no_schema_and_may_have_several_ops():
    ops = [_op("op1", schema=()), _op("op2", operation="limit", schema=(), limit=5)]
    response = _response(operation="union", ops=ops, left=("category",), right=("category",), keys=())

    assert post_combine_problems(response) == []


def test_a_join_with_no_op_is_left_alone():
    assert post_combine_problems(_response(ops=[])) == []


def test_a_bare_name_on_both_sides_in_the_schema_is_ambiguous():
    response = _response(left=("category", "revenue"), right=("category", "film", "revenue"),
                         ops=[_op(schema=("film", "revenue"))])

    [problem] = post_combine_problems(response)

    assert "'revenue' is on both sides" in problem


# --- retries and prompt ---------------------------------------------------------------


def test_the_decomposer_may_be_asked_again_twice():
    assert MAX_RETRIES == 2


def test_the_prompt_and_the_feedback_say_where_a_join_group_ends():
    for text in (DECOMPOSER_SYSTEM_PROMPT, DECOMPOSER_RETRY_FEEDBACK):
        assert "expected_schema" in text and "question order" in text


# --- replay of the recorded Pagila 001 decomposition -----------------------------------


def test_the_recorded_pagila_001_decomposition_answers_in_the_question_columns():
    path = Path(__file__).resolve().parents[1] / "fixtures" / "pagila_001_split_decomposition.json"
    response = DecomposerResponse.model_validate(json.loads(path.read_text(encoding="utf-8")))
    top_id, films_id = (sq.id for sq in response.sub_queries)
    frames = {
        top_id: pl.DataFrame({"category_name": ["Sports"], "category_revenue": [900.0]}),
        films_id: pl.DataFrame({
            "film_title": list("ABCDEFG"), "category_name": ["Sports"] * 6 + ["Drama"],
            "film_revenue": [7.0, 1.0, 6.0, 2.0, 5.0, 3.0, 99.0]}),
    }
    engine = PolarsDuckdbEngine.__new__(PolarsDuckdbEngine)
    engine.load_scan = lambda ref: frames[ref].lazy()

    assert post_combine_problems(response) == []
    out = AggregationService(engine).execute(build_execution_dag(response), {top_id: top_id, films_id: films_id})

    [result] = (frame.collect() for frame in out.values())
    assert result.columns == ["film_title", "category_name", "film_revenue"]
    assert result["film_title"].to_list() == ["A", "C", "E", "F", "D"]
