"""The tier 1 gold plans stay complete and consistent with the gold dataset.

Key-free. The run itself -- each plan validated, generated, executed and
compared per role -- is ``tests/e2e/test_benchmark_tier1.py``.
"""
from __future__ import annotations

import pytest

from nl2sql.evaluation.gold import load_gold_dataset
from nl2sql.evaluation.tier1 import decomposer_response, load_gold_plans
from nl2sql.pipeline.nodes.ast_planner.schemas import PlanModel
from nl2sql.pipeline.nodes.decomposer.schemas import DecomposerResponse

DATASET = load_gold_dataset()
ANSWERABLE = [q for q in DATASET if q.gold_sql is not None]
PLANS = load_gold_plans()


def test_every_answerable_question_has_exactly_one_plan():
    assert set(PLANS) == {q.id for q in ANSWERABLE}


def test_every_plan_is_a_plan_model():
    assert all(isinstance(p, PlanModel) for p in PLANS.values())


@pytest.mark.parametrize("q", ANSWERABLE, ids=lambda q: q.id)
def test_plan_reads_exactly_the_needed_tables(q):
    # The RBAC outcome in ``expected`` is derived from ``needed_tables``, so a
    # plan reading any other table would be scored against the wrong outcome.
    assert {t.name for t in PLANS[q.id].tables} == set(q.needed_tables)


@pytest.mark.parametrize("q", ANSWERABLE, ids=lambda q: q.id)
def test_plan_selects_as_many_columns_as_the_gold_result(q):
    if q.gold_result:
        assert len(PLANS[q.id].select_items) == len(q.gold_result[0])


@pytest.mark.parametrize("q", ANSWERABLE, ids=lambda q: q.id)
def test_decomposer_response_matches_the_plan(q):
    plan = PLANS[q.id]
    response = DecomposerResponse.model_validate(decomposer_response(q, plan))
    (sub_query,) = response.sub_queries
    assert sub_query.datasource_id == "chinook"
    assert [c.name for c in sub_query.expected_schema] == [s.alias for s in plan.select_items]
    assert all(s.alias for s in plan.select_items)
