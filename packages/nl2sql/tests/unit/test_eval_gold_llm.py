"""The gold-plan fake LLM mode plans runs on: every answer comes from the case, keyed by schema.

Key-free. The run itself -- each plan validated, generated, executed and
scored per role -- is ``tests/e2e/test_evals_plans.py``.
"""
from __future__ import annotations

import pytest

from nl2sql.evaluation.gold_llm import RESPONSES, GoldPlanLLM, answerability, decomposition, gold_plans
from nl2sql.evaluation.suite import CHINOOK_SUITE_PATH, load_suite
from nl2sql.pipeline.nodes.ast_planner.schemas import PlanModel
from nl2sql.pipeline.nodes.datasource_resolver.schemas import AnswerabilityResponse
from nl2sql.pipeline.nodes.decomposer.schemas import DecomposerResponse

CASES = load_suite(CHINOOK_SUITE_PATH).cases
ANSWERABLE = [c for c in CASES if c.sql is not None]
PLANS = gold_plans(CASES)


def test_every_answerable_case_has_its_plan_as_a_plan_model():
    assert set(PLANS) == {c.id for c in ANSWERABLE}
    assert all(isinstance(p, PlanModel) for p in PLANS.values())


@pytest.mark.parametrize("c", CASES, ids=lambda c: c.id)
def test_the_answerability_verdict_follows_the_case(c):
    verdict = AnswerabilityResponse.model_validate(answerability(c))
    assert verdict.answerable_datasource_ids == ([] if c.sql is None else c.datasources)


@pytest.mark.parametrize("c", ANSWERABLE, ids=lambda c: c.id)
def test_the_decomposition_matches_the_plan(c):
    plan = PLANS[c.id]
    (sub_query,) = DecomposerResponse.model_validate(decomposition(c, plan)).sub_queries
    assert sub_query.datasource_id == "chinook"
    assert [col.name for col in sub_query.expected_schema] == [s.alias for s in plan.select_items]


def test_every_structured_output_is_answered_from_one_table_keyed_by_its_schema():
    # Wave B's planner answer wraps PlanModel: it is one more entry here.
    assert set(RESPONSES) == {"AnswerabilityResponse", "DecomposerResponse", "PlanModel", "AggregatedResponse",
                              "plain"}
    llm = GoldPlanLLM(PLANS)
    assert [rule.name for rule in llm.server.rules] == list(RESPONSES)
    llm.select(ANSWERABLE[0])
    assert PlanModel.model_validate(llm.respond("PlanModel")) == PLANS[ANSWERABLE[0].id]


def test_a_response_can_be_replaced_per_fake():
    llm = GoldPlanLLM(PLANS)
    llm.responses["AggregatedResponse"] = lambda fake: {"summary": f"about {fake.case.id}"}
    llm.select(CASES[0])
    assert llm.respond("AggregatedResponse") == {"summary": f"about {CASES[0].id}"}
    assert GoldPlanLLM(PLANS).respond("AggregatedResponse") != {"summary": f"about {CASES[0].id}"}


def test_a_case_without_a_plan_is_named():
    llm = GoldPlanLLM({})
    llm.select(ANSWERABLE[0])
    with pytest.raises(KeyError, match=ANSWERABLE[0].id):
        llm.respond("PlanModel")
