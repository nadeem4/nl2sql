"""Knowledge in the prompts (docs/architecture/wave_b.md, section 4; K2).

The schema retriever reads the run's pinned knowledge: the chosen
descriptions, the roles (the glossary's, else the ones the snapshot proves),
units, grains and code values go into the schema block; glossary joins become
relationships; matched terms and the rules of the sub-query's tables become
``knowledge_used``, which the planner and the refiner send as
``[BUSINESS_CONTEXT]``. The planner's structured output is ``PlannerAnswer``:
its default plan is used, and each alternative passes ``plan.after``.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from nl2sql.hooks import Allow, Deny, HookBinding, HookRunner, Modify
from nl2sql.knowledge.match import match_terms, rules_for
from nl2sql.knowledge.merge import LoadedSource, merge
from nl2sql.knowledge.models import RuleUse, TermMatch
from nl2sql.knowledge.render import fit_business_context, render_business_context
from nl2sql.pipeline.nodes.ast_planner.node import ASTPlannerNode
from nl2sql.pipeline.nodes.ast_planner.prompts import PLANNER_PROMPT
from nl2sql.pipeline.nodes.ast_planner.schemas import PlanAlternative, PlannerAnswer, PlanModel
from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery
from nl2sql.pipeline.nodes.refiner.node import RefinerNode
from nl2sql.pipeline.nodes.refiner.prompts import REFINER_PROMPT
from nl2sql.pipeline.nodes.schema_retriever.node import SchemaRetrieverNode
from nl2sql.pipeline.nodes.schema_retriever.schema import render_schema_for_prompt
from nl2sql.pipeline.state import SubgraphExecutionState
from nl2sql.policies import UserContext
from nl2sql.testing.runtime import fake_runtime
from nl2sql_sdk.knowledge import Glossary
from nl2sql_sdk.schema import (
    ColumnContract,
    ColumnMetadata,
    SchemaContract,
    SchemaMetadata,
    SchemaSnapshot,
    TableContract,
    TableMetadata,
    TableRef,
)

GLOSSARY = {
    "version": 1,
    "datasources": {
        "shop": {
            "tables": {"orders": {"description": "One row per order. Total includes tax.",
                                  "notes": "Kept for seven years."}},
            "columns": {
                "orders.total": {"description": "Order total in USD.", "role": "money", "unit": "USD"},
                "orders.created_at": {"grain": "day"},
                "orders.status": {"role": "enum", "values": {"1": "open", "2": "shipped"}},
                "orders.id": {"role": "quantity"},
            },
            "joins": [{"from": "orders.customer_id", "to": "customers.id", "cardinality": "many-to-one"}],
            "terms": [
                {"term": "revenue", "synonyms": ["sales", "turnover"], "definition": "Sum of orders.total less tax.",
                 "columns": ["orders.total"]},
                {"term": "invoiced total", "synonyms": ["sales", "billings"], "definition": "Sum of orders.total.",
                 "columns": ["orders.total"]},
                {"term": "churn", "definition": "Customers with no order in a year.", "tables": ["customers"]},
            ],
            "rules": [
                {"id": "no_test_customers", "text": "Leave out customers whose email ends in @example.com.",
                 "tables": ["customers"]},
                {"id": "usd", "text": "Amounts are in US dollars."},
                {"id": "no_cancelled", "text": "Cancelled orders do not count.", "tables": ["orders"]},
            ],
        }
    },
}

ROLES = {"admin": {"description": "Everything", "allow": ["*"]},
         "analyst": {"description": "All but pay", "allow": ["shop"], "deny": ["shop.main.salaries"]}}
ANALYST = UserContext(roles=["analyst"])


def knowledge_of(data=GLOSSARY):
    glossary = Glossary.model_validate(data)
    return merge([LoadedSource(type="yaml", glossary=glossary, sha256=None, path=None,
                               loaded_at=datetime.now(timezone.utc))])


def _table(name, columns, comment=None, column_comments=None, foreign_keys=()):
    ref = TableRef(schema_name="main", table_name=name)
    contract = TableContract(table=ref, columns={
        column: ColumnContract(name=column, data_type=spec.split()[0], is_primary_key=spec.endswith(" pk"))
        for column, spec in columns.items()}, foreign_keys=list(foreign_keys))
    metadata = TableMetadata(table=ref, description=comment, columns={
        column: ColumnMetadata(name=column, description=text) for column, text in (column_comments or {}).items()})
    return ref.full_name, contract, metadata


def snapshot():
    tables = [
        _table("customers", {"id": "INTEGER pk", "name": "TEXT", "email": "TEXT"}, comment="Customer master.",
               column_comments={"email": "Contact email."}),
        _table("orders", {"id": "INTEGER pk", "customer_id": "INTEGER", "total": "REAL", "created_at": "DATE",
                          "status": "INTEGER"}, comment="Orders table."),
        _table("salaries", {"id": "INTEGER pk", "amount": "REAL"}),
    ]
    return SchemaSnapshot(
        contract=SchemaContract(datasource_id="shop", engine_type="sqlite", tables={k: c for k, c, _ in tables}),
        metadata=SchemaMetadata(datasource_id="shop", engine_type="sqlite", tables={k: m for k, _, m in tables}))


def retriever(knowledge=None, policies=None, settings=None, term_search=None):
    """``term_search(query, datasource_id, k)`` stands in for the index's term search; none: no such search."""
    vector_store = SimpleNamespace(snapshot=lambda _id: snapshot(),
                                   **({"retrieve_term_candidates": term_search} if term_search else {}))
    registry = SimpleNamespace(get_preferred_tables=lambda _id: [], get_dialect=lambda _id: "sqlite")
    ctx = fake_runtime(vector_store=vector_store, state=None, ds_registry=registry,
                       policies=policies or {"roles": ROLES}, settings=settings,
                       knowledge=knowledge_of() if knowledge is None else knowledge)
    return SchemaRetrieverNode(ctx)


def retrieve(intent, *, question="", user=ANALYST, **kwargs):
    state = SubgraphExecutionState(trace_id="t", user_query=question or intent, user_context=user,
                                   sub_query=SubQuery(id="sq1", datasource_id="shop", intent=intent))
    return retriever(**kwargs)(state)


def block(out):
    return [json.loads(line) for line in render_schema_for_prompt(out["relevant_tables"]).splitlines()]


def table_row(out, name):
    return next(row for row in block(out) if row["name"] == name)


def column_row(out, table, column):
    return next(c for c in table_row(out, table)["columns"] if c["name"] == column)


# ---------------------------------------------------------------- the schema block


def test_description_precedence_in_the_schema_block():
    out = retrieve("orders by customer")

    # The glossary's wins over the database's comment; the comment fills what the glossary does not set.
    assert table_row(out, "orders")["description"] == "One row per order. Total includes tax."
    assert table_row(out, "customers")["description"] == "Customer master."
    assert column_row(out, "orders", "total")["description"] == "Order total in USD."
    assert column_row(out, "customers", "email")["description"] == "Contact email."


def test_column_role_unit_and_values_reach_the_schema_block():
    out = retrieve("orders by customer")

    total = column_row(out, "orders", "total")
    assert (total["role"], total["unit"]) == ("money", "USD")
    status = column_row(out, "orders", "status")
    assert status["role"] == "enum" and status["values"] == {"1": "open", "2": "shipped"}
    # Derived from the type, with the glossary's grain.
    created = column_row(out, "orders", "created_at")
    assert (created["role"], created["grain"]) == ("date", "day")
    # A key is an identifier, unless the glossary says otherwise.
    assert column_row(out, "customers", "id")["role"] == "identifier"
    assert column_row(out, "orders", "id")["role"] == "quantity"
    assert "role" not in column_row(out, "customers", "name")


def test_glossary_join_is_a_relationship_between_visible_tables():
    out = retrieve("orders by customer")

    [relationship] = table_row(out, "orders")["relationships"]
    assert relationship["from_columns"] == ["customer_id"] and relationship["to_columns"] == ["id"]
    assert relationship["cardinality"] == "many-to-one" and relationship["source"] == "glossary"


def test_without_knowledge_the_tables_carry_only_the_schema():
    from nl2sql.knowledge.merge import Knowledge

    out = retrieve("orders by customer", knowledge=Knowledge())

    assert table_row(out, "orders")["description"] == "Orders table."
    assert "relationships" not in table_row(out, "orders")
    assert out.get("knowledge_used") is None


# ---------------------------------------------------------------- terms and rules


def test_term_matches_by_name_and_synonym():
    terms = knowledge_of().get("shop").terms
    sources = {"revenue": "yaml", "invoiced total": "yaml", "churn": "yaml"}

    by_name = match_terms(terms, ["What was REVENUE, by month?"], sources=sources, top_k=8)
    assert [(m.term, m.score) for m in by_name] == [("revenue", 1.0)]
    by_synonym = match_terms(terms, ["turnover by month"], sources=sources, top_k=8)
    assert [(m.term, m.score) for m in by_synonym] == [("revenue", 0.9)]
    # A phrase matches as whole words only, case and punctuation ignored.
    assert match_terms(terms, ["revenues by month"], sources=sources, top_k=8) == []
    assert [m.term for m in match_terms(terms, ["the invoiced-total, please"], sources=sources, top_k=8)] == [
        "invoiced total"]
    assert by_name[0].source == "yaml" and by_name[0].columns == ["orders.total"]


def test_match_terms_adds_a_term_the_index_found_above_the_floor():
    terms = knowledge_of().get("shop").terms

    matches = match_terms(terms, ["income last year"], sources={}, top_k=8,
                          similar={"revenue": 0.8, "churn": 0.59, "unknown": 0.99}, floor=0.6)

    assert [(m.term, m.score) for m in matches] == [("revenue", 0.8)]
    # A name in the text keeps its lexical score, whatever the index said.
    named = match_terms(terms, ["revenue"], sources={}, top_k=8, similar={"revenue": 0.7}, floor=0.6)
    assert [(m.term, m.score) for m in named] == [("revenue", 1.0)]


def test_terms_are_matched_by_similarity_through_the_index():
    from nl2sql.indexing.models import TermCandidate

    asked = []

    def term_search(query, datasource_id, k):
        asked.append((query, datasource_id, k))
        return [TermCandidate(datasource_id="shop", term="revenue", score=0.8),
                TermCandidate(datasource_id="shop", term="churn", score=0.5)]

    out = retrieve("income last year", term_search=term_search)

    assert [(t.term, t.score) for t in out["knowledge_used"].terms] == [("revenue", 0.8)]
    assert asked == [("income last year", "shop", 8)]


def test_similarity_never_brings_in_a_term_the_caller_may_not_see():
    from nl2sql.indexing.models import TermCandidate

    data = {"version": 1, "datasources": {"shop": {"terms": [
        {"term": "payroll", "definition": "What we pay people.", "tables": ["salaries"]}]}}}

    out = retrieve("what do we spend on people", knowledge=knowledge_of(data),
                   term_search=lambda *_: [TermCandidate(datasource_id="shop", term="payroll", score=0.95)])

    assert out.get("knowledge_used") is None


def test_a_failing_term_search_leaves_the_lexical_match():
    def broken(*_):
        raise RuntimeError("the vector store is down")

    out = retrieve("revenue by month", term_search=broken)

    assert [(t.term, t.score) for t in out["knowledge_used"].terms] == [("revenue", 1.0)]


def test_both_terms_sharing_a_synonym_are_sent():
    out = retrieve("sales by country")

    used = out["knowledge_used"]
    assert sorted((t.term, t.score) for t in used.terms) == [("invoiced total", 0.9), ("revenue", 0.9)]
    assert used.datasource_id == "shop" and used.digest == knowledge_of().digest("shop")


def test_terms_are_matched_in_the_question_too_and_capped_at_top_k():
    terms = knowledge_of().get("shop").terms
    matches = match_terms(terms, ["count orders", "churn and revenue and billings"], sources={}, top_k=2)

    # Best first: the names (1.0), then the synonyms; the rest are cut.
    assert [(m.term, m.score) for m in matches] == [("churn", 1.0), ("revenue", 1.0)]


def test_rules_are_sent_by_table():
    rules = knowledge_of().get("shop").rules

    on_orders = rules_for(rules, {"orders"}, snapshot(), sources={})
    assert [r.id for r in on_orders] == ["usd", "no_cancelled"]
    on_both = rules_for(rules, {"orders", "customers"}, snapshot(), sources={})
    assert [r.id for r in on_both] == ["no_test_customers", "usd", "no_cancelled"]


def test_retriever_sends_the_rules_of_the_tables_it_sends():
    out = retrieve("orders by customer")

    assert [r.id for r in out["knowledge_used"].rules] == ["no_test_customers", "usd", "no_cancelled"]


# ---------------------------------------------------------------- [BUSINESS_CONTEXT]


def test_business_context_is_one_sorted_json_object():
    terms = [TermMatch(term="revenue", definition="Sum.", columns=["orders.total"], source="yaml", score=1.0)]
    rules = [RuleUse(id="usd", text="Amounts are in US dollars.", source="yaml")]

    assert render_business_context(terms, rules) == (
        '{"rules":[{"id":"usd","text":"Amounts are in US dollars."}],'
        '"terms":[{"columns":["orders.total"],"definition":"Sum.","term":"revenue"}]}')
    assert render_business_context([], []) == "{}"


def test_block_is_cut_by_score_then_rules_in_reverse():
    terms = [TermMatch(term=f"t{i}", definition="x" * 40, source="yaml", score=1.0 - i / 10) for i in range(3)]
    rules = [RuleUse(id=f"r{i}", text="y" * 20, source="yaml") for i in range(2)]
    full = render_business_context(terms, rules)

    kept_terms, kept_rules, dropped = fit_business_context(terms, rules, max_chars=len(full) - 1)
    assert [t.term for t in kept_terms] == ["t0", "t1"] and dropped == ["t2"]
    kept_terms, kept_rules, dropped = fit_business_context(terms, rules, max_chars=60)
    assert kept_terms == [] and [r.id for r in kept_rules] == ["r0"] and dropped == ["t2", "t1", "t0", "r1"]
    assert len(render_business_context(kept_terms, kept_rules)) <= 60


def _planner(answer, hooks=None):
    llm = MagicMock()
    llm.with_structured_output.return_value = llm
    ctx = fake_runtime(llm_registry=MagicMock(), state=None, **({"hooks": hooks} if hooks else {}))
    ctx.llm_registry.get_llm.return_value = llm
    ctx.llm_registry.effective.return_value = None
    node = ASTPlannerNode(ctx)
    node.chain = MagicMock()
    node.chain.invoke.return_value = answer
    return node


def _plan(table="orders", reasoning="ok") -> PlanModel:
    return PlanModel.model_validate({
        "tables": [{"name": table, "alias": "t1"}],
        "select_items": [{"alias": "n", "expr": {"kind": "func", "func_name": "COUNT", "is_aggregate": True,
                                                  "args": [{"kind": "column", "alias": "t1", "column_name": "id"}]}}],
        "reasoning": reasoning})


def _planned_state(**fields):
    retrieved = retrieve("sales by country")
    return SubgraphExecutionState(trace_id="t", user_query="Sales by country?", user_context=ANALYST,
                                  sub_query=SubQuery(id="sq1", datasource_id="shop", intent="sales by country"),
                                  relevant_tables=retrieved["relevant_tables"],
                                  knowledge_used=retrieved["knowledge_used"], **fields)


def test_business_context_is_last_before_feedback_and_query():
    node = _planner(PlannerAnswer(plan=_plan()))
    node(_planned_state())

    inputs = node.chain.invoke.call_args.args[0]
    system, human = PLANNER_PROMPT.format_messages(**inputs)
    assert "[BUSINESS_CONTEXT]\n" not in system.content, "the system message is the cache prefix"
    text = human.content
    assert text.index("[BUSINESS_CONTEXT]") < text.index("[FEEDBACK]") < text.index("[USER_QUERY]")
    context = json.loads(text.split("[BUSINESS_CONTEXT]\n", 1)[1].split("\n", 1)[0])
    assert sorted(t["term"] for t in context["terms"]) == ["invoiced total", "revenue"]
    assert text.rstrip().endswith("[USER_QUERY]\nsales by country")


def test_refiner_sends_the_same_business_context():
    llm = MagicMock()
    ctx = fake_runtime(llm_registry=MagicMock())
    ctx.llm_registry.get_llm.return_value = llm
    node = RefinerNode(ctx)
    node.chain = MagicMock()
    node.chain.invoke.return_value = "Use orders."

    node(_planned_state())

    inputs = node.chain.invoke.call_args.args[0]
    _, human = REFINER_PROMPT.format_messages(**inputs)
    context = human.content.split("### Business Context\n", 1)[1].split("\n", 1)[0]
    assert sorted(t["term"] for t in json.loads(context)["terms"]) == ["invoiced total", "revenue"]


# ---------------------------------------------------------------- PlannerAnswer


def test_planner_answer_default_plan_is_used_without_alternatives():
    node = _planner(PlannerAnswer(plan=_plan(reasoning="default")))

    response = node(_planned_state())["ast_planner_response"]

    assert response.plan.reasoning == "default" and response.plan_source == "llm"
    assert response.alternatives == [] and response.ambiguous_phrase is None


def _hooks(decide):
    return HookRunner([HookBinding(name="h", hook=decide, points=frozenset({"plan.after"}), on_error="closed",
                                   approver_roles=None)])


def test_alternative_denied_by_plan_after_is_not_offered():
    answer = PlannerAnswer(plan=_plan(reasoning="revenue"), ambiguous_phrase="sales", alternatives=[
        PlanAlternative(label="Invoiced total", plan=_plan(table="invoices", reasoning="invoiced")),
        PlanAlternative(label="Units sold", plan=_plan(reasoning="units")),
    ])
    hooks = _hooks(lambda ctx: Deny(reason="no invoices") if ctx.plan.tables[0].name == "invoices" else Allow())

    response = _planner(answer, hooks)(_planned_state())["ast_planner_response"]

    assert response.plan.reasoning == "revenue" and response.ambiguous_phrase == "sales"
    assert [(a.label, a.plan.reasoning) for a in response.alternatives] == [("Units sold", "units")]
    # What the planner offered stays whole, so the plan is never cached (D47).
    assert [a.label for a in response.alternatives_offered] == ["Invoiced total", "Units sold"]


def test_alternative_modified_by_plan_after_is_offered_as_modified():
    answer = PlannerAnswer(plan=_plan(reasoning="revenue"), ambiguous_phrase="sales", alternatives=[
        PlanAlternative(label="Units sold", plan=_plan(reasoning="units"))])

    def hook(ctx):
        if ctx.plan.reasoning == "units":
            return Modify(plan=ctx.plan.model_copy(update={"limit": 10}))
        return Allow()

    response = _planner(answer, _hooks(hook))(_planned_state())["ast_planner_response"]

    assert [a.plan.limit for a in response.alternatives] == [10]


# ---------------------------------------------------------------- review fixes (#340)


def test_the_denied_name_pattern_is_built_once_per_sub_query_not_per_comment(monkeypatch):
    import nl2sql.knowledge.scope as scope

    calls = []
    real = scope.denied_table_names
    monkeypatch.setattr(scope, "denied_table_names", lambda *a: calls.append(1) or real(*a))

    retrieve("orders by customer")

    # Once for the glossary's filter (visible) and once for the comments' pattern, whatever the comment count.
    assert len(calls) == 2


def test_the_explanation_reads_the_runs_pinned_knowledge():
    from nl2sql.knowledge.merge import Knowledge
    from nl2sql.knowledge.scope import caller_knowledge

    def runtime(knowledge):
        return fake_runtime(vector_store=SimpleNamespace(snapshot=lambda _id: snapshot()),
                            policies={"roles": ROLES}, knowledge=knowledge)

    state = {"user_context": ANALYST, "subgraph_outputs": {"sql_agent:sq1:t": {"sub_query": {"datasource_id": "shop"}}}}
    live, pinned = runtime(Knowledge()), runtime(knowledge_of())

    # A reload swapped the live glossary for an empty one mid-run; the run's own is the one its prompts used.
    assert caller_knowledge(live, state) == {}
    shown = caller_knowledge(live, {**state, "pinned_runtime": pinned})
    assert "orders" in shown["shop"].tables
