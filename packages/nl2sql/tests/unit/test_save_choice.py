"""Saving a clarification pick: ``engine.knowledge.save_choice`` (wave_b.md, section 7; D50, D60, L2).

A pick becomes a ``term`` proposal; only an admin's accept writes the glossary, and a term of the same name is
replaced. A real engine on a small SQLite database, as the review queue's tests build.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from test_review_queue import ADMIN, ANALYST, _engine, _project

from nl2sql.clarify.models import ClarificationChoice, ClarificationOption, ClarificationRequest
from nl2sql.explain.render import explain_plan
from nl2sql.pipeline.nodes.ast_planner.schemas import PlanModel
from nl2sql.review.models import ReviewError, ReviewErrorCode


def _sum(table: str, column: str) -> PlanModel:
    return PlanModel.model_validate({
        "tables": [{"name": table, "alias": "t"}],
        "select_items": [{"alias": "sales", "expr": {
            "kind": "func", "func_name": "SUM", "is_aggregate": True,
            "args": [{"kind": "column", "alias": "t", "column_name": column}]}}]})


def _choice(engine, *, table="orders", column="total", option_id="b", phrase="sales"):
    plans = {"a": ("Gross", _sum("orders", "id")), "b": ("Order total", _sum(table, column))}
    options = [ClarificationOption(id=i, label=label, plan=plan.model_dump(mode="json"),
                                   explanation=explain_plan(plan, datasource_id="shop"))
               for i, (label, plan) in plans.items()]
    ds_id = "shop"
    request = ClarificationRequest.build(
        question="What were sales?", asker_user_id="bob", asker_tenant_id=None, phrase=phrase, datasource_id=ds_id,
        schema_version=engine._runtime.vector_store.schema_version(ds_id) or "v1",
        knowledge_digest=engine._runtime.knowledge.digest(ds_id), sub_query={"id": "sq1", "datasource_id": ds_id},
        options=options)
    return ClarificationChoice(request=request, option_id=option_id)


@pytest.fixture
def engine(tmp_path):
    with _engine(_project(tmp_path)) as engine:
        assert engine.index.build().ok
        yield engine


def test_save_choice_creates_a_term_proposal(engine):
    proposal = engine.knowledge.save_choice(_choice(engine), user_context=ANALYST)

    assert (proposal.kind, proposal.origin, proposal.status) == ("term", "clarification", "proposed")
    assert proposal.datasource_id == "shop" and proposal.target == "sales"
    assert proposal.change == {"term": "sales", "definition": "Order total", "columns": ["orders.total"],
                               "tables": ["orders"]}
    assert proposal.tables == ["shop.main.orders"] and proposal.created_by == "bob"
    # Nothing reached the glossary: the queue holds it.
    assert [p.id for p in engine.proposals.list(user_context=ADMIN, kind="term")] == [proposal.id]
    assert engine._runtime.knowledge.get("shop").terms == []


def test_a_term_name_other_than_the_phrase_keeps_the_phrase_as_a_synonym(engine):
    proposal = engine.knowledge.save_choice(_choice(engine), user_context=ANALYST, term="order total")

    assert proposal.target == "order total" and proposal.change["synonyms"] == ["sales"]


def test_saving_needs_a_caller_whose_scope_reaches_the_tables(engine):
    choice = _choice(engine, table="salaries", column="amount")

    with pytest.raises(ReviewError) as error:
        engine.knowledge.save_choice(choice, user_context=ANALYST)
    assert error.value.code == ReviewErrorCode.NOT_FOUND
    assert engine.proposals.list(user_context=ADMIN, kind="term") == []
    assert engine.knowledge.save_choice(choice, user_context=ADMIN).tables == ["shop.main.salaries"]


def test_saving_requires_the_caller_and_a_consistent_request(engine):
    choice = _choice(engine)

    with pytest.raises(TypeError):
        engine.knowledge.save_choice(choice, user_context=None)
    with pytest.raises(ValueError, match="changed"):
        engine.knowledge.save_choice(choice.model_copy(update={"option_id": "z"}), user_context=ANALYST)
    edited = choice.request.model_copy(update={"phrase": "revenue"})
    with pytest.raises(ValueError, match="changed"):
        engine.knowledge.save_choice(ClarificationChoice(request=edited, option_id="b"), user_context=ANALYST)


def test_accepting_an_existing_term_replaces_it(engine, tmp_path: Path):
    engine.proposals.accept(
        engine.knowledge.save_choice(_choice(engine, column="id"), user_context=ANALYST).id, by=ADMIN)
    proposal = engine.knowledge.save_choice(_choice(engine), user_context=ANALYST)
    engine.proposals.accept(proposal.id, by=ADMIN)

    glossary = yaml.safe_load((tmp_path / "glossary.yaml").read_text(encoding="utf-8"))
    [term] = glossary["datasources"]["shop"]["terms"]
    assert term["term"] == "sales" and term["columns"] == ["orders.total"] and term["definition"] == "Order total"
    assert [t.term for t in engine._runtime.knowledge.get("shop").terms] == ["sales"]
