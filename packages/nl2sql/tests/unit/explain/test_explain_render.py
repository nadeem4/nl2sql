"""Why this answer: the explanation is rendered from the typed plan and state, never by a model (wave_b.md, section 8).

Every test builds its state by hand (X1 comes before K2 and L1 fill
``knowledge_used`` and ``alternatives``), and the golden files hold the
rendering of every Chinook gold plan, so a renderer change is a reviewed diff.
A missing golden file is written and the test fails: review it, commit it.
"""
from __future__ import annotations

import ast
import json
import typing
from pathlib import Path

import pytest

from nl2sql.api.query_api import result_from_state
from nl2sql.common.errors import ErrorCode, ErrorSeverity, PipelineError, RunWarning
from nl2sql.evaluation.suite import CHINOOK_SUITE_PATH, load_suite
from nl2sql.explain import phrases
from nl2sql.explain.models import TermUse
from nl2sql.explain.render import explain_plan, explain_state
from nl2sql.knowledge.models import KnowledgeUsed, RuleUse, TermMatch
from nl2sql.pipeline.nodes.ast_planner.schemas import Expr, PlanAlternative, PlanModel
from nl2sql.pipeline.nodes.curated_matcher.schemas import VerifiedMatch
from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery
from nl2sql.pipeline.subgraphs.schemas import SubgraphOutput
from nl2sql_sdk.knowledge import DatasourceKnowledge

GOLDEN = Path(__file__).parent / "golden"
GOLD_CASES = [c for c in load_suite(CHINOOK_SUITE_PATH).cases if c.plan]


def col(alias, name):
    return {"kind": "column", "alias": alias, "column_name": name}


def lit(value):
    return {"kind": "literal", "value": value}


def eq(left, right):
    return {"kind": "binary", "op": "=", "left": left, "right": right}


def fn(name, *args, distinct=False):
    return {"kind": "func", "func_name": name, "args": list(args), "distinct": distinct}


REVENUE_BY_COUNTRY = PlanModel.model_validate({
    "tables": [{"name": "InvoiceLine", "alias": "il"}, {"name": "Invoice", "alias": "i"},
               {"name": "Customer", "alias": "c"}],
    "joins": [
        {"left_alias": "il", "right_alias": "i", "condition": eq(col("il", "InvoiceId"), col("i", "InvoiceId"))},
        {"left_alias": "i", "right_alias": "c", "condition": eq(col("i", "CustomerId"), col("c", "CustomerId"))},
    ],
    "select_items": [
        {"expr": col("c", "Country")},
        {"alias": "revenue", "expr": fn("SUM", {"kind": "binary", "op": "*", "left": col("il", "UnitPrice"),
                                                "right": col("il", "Quantity")})},
    ],
    "where": eq(fn("DATE_PART", lit("year"), col("i", "InvoiceDate")), lit(2012)),
    "group_by": [{"expr": col("c", "Country")}],
    "order_by": [{"expr": {"kind": "column", "column_name": "revenue"}, "direction": "desc"}],
    "limit": 10,
    "reasoning": "Revenue is unit price times quantity.",
})


def _output(plan=REVENUE_BY_COUNTRY, **fields):
    return SubgraphOutput(subgraph_id="sg1", sub_query=SubQuery(id="sq1", datasource_id="chinook",
                                                                intent="revenue by country in 2012"),
                          plan=plan, sql_draft="SELECT 1", status="success", **fields)


def _golden_text(step) -> str:
    return json.dumps(step.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n"


# ---------------------------------------------------------------- golden

@pytest.mark.parametrize("case", GOLD_CASES, ids=[c.id for c in GOLD_CASES])
def test_every_chinook_gold_plan_renders(case):
    step = explain_plan(PlanModel.model_validate(case.plan), datasource_id="chinook", sub_query_id=case.id)
    path = GOLDEN / f"{case.id}.json"
    if not path.exists():
        path.write_text(_golden_text(step), encoding="utf-8")
        pytest.fail(f"Wrote {path.name}: review the rendering and commit it.")
    assert _golden_text(step) == path.read_text(encoding="utf-8"), (
        f"The rendering of {case.id} changed. If the change is right, delete {path.name} and run again.")


def test_no_golden_file_is_left_over():
    assert sorted(p.stem for p in GOLDEN.glob("*.json")) == sorted(c.id for c in GOLD_CASES)


def test_rendering_is_deterministic():
    for case in GOLD_CASES:
        plan = PlanModel.model_validate(case.plan)
        first = explain_plan(plan, datasource_id="chinook", sub_query_id=case.id)
        again = explain_plan(PlanModel.model_validate(json.loads(plan.model_dump_json())),
                             datasource_id="chinook", sub_query_id=case.id)
        assert first == again
    state = {"subgraph_outputs": {"sg1": _output()}}
    assert explain_state(state) == explain_state(state)


# ---------------------------------------------------------------- phrases

def test_each_expr_kind_has_a_phrase():
    kinds = typing.get_args(Expr.model_fields["kind"].annotation)
    assert set(kinds) <= set(phrases.KIND_PHRASES)
    ops = set(typing.get_args(typing.get_args(Expr.model_fields["op"].annotation)[0]))
    # Each table is checked on its own: a binary operator needs a binary phrase, a unary one a unary phrase.
    assert ops - {"NOT"} <= set(phrases.BINARY_PHRASES)
    assert {"NOT", "+", "-"} <= set(phrases.UNARY_PHRASES)

    names = phrases.Names({"t": "Track", "g": "Genre", "i": "Invoice"})

    def say(expr):
        return phrases.phrase(Expr.model_validate(expr), names)

    assert say(eq(col("g", "Name"), lit("Rock"))) == "Genre name is 'Rock'"
    assert say({"kind": "binary", "op": "IN", "left": col("g", "Name"),
                "right": fn("TUPLE", lit("Rock"), lit("Jazz"))}) == "Genre name is one of 'Rock', 'Jazz'"
    assert say({"kind": "binary", "op": "LIKE", "left": col("t", "Name"), "right": lit("A%")}) == \
        "Track name matches 'A%'"
    assert say({"kind": "binary", "op": "IS", "left": col("t", "Composer"),
                "right": {"kind": "literal", "is_null": True}}) == "Track composer is empty"
    assert say(fn("SUM", col("t", "Milliseconds"))) == "total of Track milliseconds"
    assert say(fn("COUNT", col("t", "GenreId"), distinct=True)) == "number of different Track genre id"
    assert say(fn("COUNT", col(None, "*"))) == "number of rows"
    assert say(fn("DATE_PART", lit("year"), col("i", "InvoiceDate"))) == "the year of Invoice date"
    assert say(fn("DATE_TRUNC", lit("month"), col("i", "InvoiceDate"))) == "the month of Invoice date"
    assert say({"kind": "case", "whens": [{"condition": {"kind": "binary", "op": ">", "left": col("t", "Bytes"),
                                                         "right": lit(1000)}, "result": lit("big")}],
                "else_expr": lit("small")}) == "depending on: 'big' when Track bytes is more than 1000; " \
                                               "otherwise 'small'"
    assert say({"kind": "unary", "op": "NOT", "expr": eq(col("g", "Name"), lit("Rock"))}) == \
        "not (Genre name is 'Rock')"
    # A function on ALLOWED_FUNCTIONS without a phrase is written as its name.
    assert say(fn("REPLACE", col("t", "Name"), lit("a"), lit("b"))) == "REPLACE(Track name, 'a', 'b')"
    # = NULL is never true in SQL; the phrase does not hide it as "is empty".
    assert say(eq(col("t", "Composer"), {"kind": "literal", "is_null": True})) == "Track composer is equal to empty"
    # AND inside OR keeps its grouping.
    assert say({"kind": "binary", "op": "OR", "left": eq(col("g", "Name"), lit("Rock")),
                "right": {"kind": "binary", "op": "AND", "left": eq(col("g", "Name"), lit("Jazz")),
                          "right": eq(col("t", "Bytes"), lit(1))}}) == \
        "Genre name is 'Rock' or (Genre name is 'Jazz' and Track bytes is 1)"


def test_a_glossary_description_names_a_table_and_a_column():
    knowledge = DatasourceKnowledge.model_validate({
        "tables": {"Invoice": {"description": "One row per customer invoice. Total includes tax."}},
        "columns": {"Invoice.Total": {"description": "Invoice total in US dollars, tax included."}},
    })
    plan = PlanModel.model_validate({"tables": [{"name": "Invoice", "alias": "i"}],
                                     "select_items": [{"alias": "total", "expr": fn("SUM", col("i", "Total"))}]})
    step = explain_plan(plan, datasource_id="chinook", knowledge=knowledge)
    assert step.tables == ["Invoice: one row per customer invoice (glossary)"]
    assert step.measures == ["total: total of invoice total in US dollars"]


# ---------------------------------------------------------------- the step

def test_a_plan_renders_every_part():
    step = explain_plan(REVENUE_BY_COUNTRY, datasource_id="chinook", sub_query_id="sq1")
    assert step.source == "generated" and step.sub_query_id == "sq1" and step.datasource_id == "chinook"
    assert step.tables == ["InvoiceLine", "Invoice", "Customer"]
    assert step.joins == ["each InvoiceLine to its Invoice, on InvoiceId (declared key)",
                          "each Invoice to its Customer, on CustomerId (declared key)"]
    assert step.filters == ["the year of Invoice date is 2012"]
    assert step.groupings == ["by Customer country"]
    assert step.measures == ["revenue: total of InvoiceLine unit price times InvoiceLine quantity"]
    assert step.order == "highest revenue first, top 10"
    assert step.text == ("Shows Customer country and revenue, by Customer country. "
                         "Reads InvoiceLine, Invoice and Customer, where the year of Invoice date is 2012. "
                         "Highest revenue first, top 10.")


def test_join_names_its_source():
    plan = PlanModel.model_validate({
        "tables": [{"name": "Track", "alias": "t"}, {"name": "MediaType", "alias": "m"},
                   {"name": "Genre", "alias": "g"}],
        "joins": [
            {"left_alias": "t", "right_alias": "m", "condition": eq(col("t", "MediaTypeId"), col("m", "MediaTypeId"))},
            {"left_alias": "t", "right_alias": "g", "join_type": "left",
             "condition": eq(col("g", "GenreId"), col("t", "GenreId"))},
        ],
        "select_items": [{"expr": col("t", "Name")}],
    })
    knowledge = DatasourceKnowledge.model_validate(
        {"joins": [{"from": "Track.MediaTypeId", "to": "MediaType.MediaTypeId", "cardinality": "many-to-one"}]})
    step = explain_plan(plan, datasource_id="chinook", knowledge=knowledge)
    assert step.joins == ["each Track to its MediaType, on MediaTypeId (glossary)",
                          "each Track with its Genre, if any, on GenreId (declared key)"]
    assert explain_plan(plan, datasource_id="chinook").joins[0].endswith("(declared key)")


def test_terms_and_rules_from_knowledge_used():
    used = KnowledgeUsed(datasource_id="chinook", digest="k1", terms=[
        TermMatch(term="revenue", definition="Sum of unit price times quantity. Excludes tax.",
                  columns=["InvoiceLine.UnitPrice", "InvoiceLine.Quantity"], source="yaml", score=0.9),
        TermMatch(term="invoiced total", definition="Sum of Invoice.Total.", columns=["Invoice.Total"],
                  source="yaml", score=0.9),
    ], rules=[RuleUse(id="no_test_customers", text="Leave test customers out.", tables=["Customer"],
                      source="yaml")])
    result = explain_state({"subgraph_outputs": {"sg1": _output(knowledge_used=used)}})
    step = result.steps[0]
    # Only the term whose columns the plan reads; every rule sent with the step.
    assert step.terms == [TermUse(term="revenue", definition="Sum of unit price times quantity. Excludes tax.",
                                  source="yaml")]
    assert step.rules == ["Leave test customers out."]


def test_readings_not_chosen_are_assumptions():
    other = PlanModel.model_validate({"tables": [{"name": "Invoice", "alias": "i"}],
                                      "select_items": [{"alias": "total", "expr": fn("SUM", col("i", "Total"))}]})
    used = KnowledgeUsed(datasource_id="chinook", digest="k1", terms=[
        TermMatch(term="revenue", columns=["InvoiceLine.UnitPrice"], source="yaml", score=0.9)])
    output = _output(knowledge_used=used, ambiguous_phrase="sales",
                     alternatives=[PlanAlternative(label="Invoiced total (tax included)", plan=other)])
    step = explain_state({"subgraph_outputs": {"sg1": output}}).steps[0]
    assert step.assumptions == ["'sales' was read as revenue; it could also mean Invoiced total (tax included)."]

    unnamed = _output(ambiguous_phrase="sales",
                      alternatives=[PlanAlternative(label="Invoiced total", plan=other),
                                    PlanAlternative(label="Units sold", plan=other)])
    step = explain_state({"subgraph_outputs": {"sg1": unnamed}}).steps[0]
    assert step.assumptions == ["'sales' was read one way; it could also mean Invoiced total, or Units sold."]


def test_verified_answer_is_explained_without_a_plan():
    sub_query = SubQuery(id="verified_top_customers_by_revenue", datasource_id="chinook", intent="top customers")
    output = SubgraphOutput(subgraph_id="v", sub_query=sub_query, plan_source="verified", sql_draft="SELECT 1",
                            status="success")
    match = VerifiedMatch(datasource_id="chinook", query_id="top_customers_by_revenue",
                          description="The customers who spent the most in a year.", params={"year": 2012, "n": 10})
    state = {"subgraph_outputs": {"v": output}, "verified_match": match}
    step = explain_state(state).steps[0]
    assert step.source == "verified" and step.sub_query_id == "verified_top_customers_by_revenue"
    assert not (step.tables or step.joins or step.filters or step.measures or step.order)
    assert step.text == ("Answered by the verified query `top_customers_by_revenue`, written by the data team, "
                         "with n = 10 and year = 2012. The customers who spent the most in a year.")
    assert result_from_state(state).explanation.steps[0] == step


def test_truncated_result_adds_an_assumption():
    step = explain_state({"subgraph_outputs": {"sg1": _output(row_cap=1000, truncated=True)}}).steps[0]
    assert step.assumptions == ["Only the first 1000 rows were read; the full result may have more."]
    assert explain_state({"subgraph_outputs": {"sg1": _output(row_cap=1000)}}).steps[0].assumptions == []


def _retry_warning(value, suggestions):
    """The recovered warning as the executor writes it: its message is the planner's retry prompt."""
    quoted = ", ".join(f"'{v}'" for v in suggestions)
    return RunWarning(code=ErrorCode.VALUE_NOT_FOUND.value, node="executor", sub_query_id="sq1",
                      message=f"No Customer.Country value is '{value}', so the query returned no rows. "
                              f"The closest existing values are {quoted}; use one of them if it is what the "
                              "question means.",
                      detail={"recovered": True})


def _country_plan(value):
    return PlanModel.model_validate({"tables": [{"name": "Customer", "alias": "c"}],
                                     "select_items": [{"expr": col("c", "FirstName")}],
                                     "where": eq(col("c", "Country"), lit(value))})


def test_value_not_found_replacement_is_an_assumption():
    fan_out = RunWarning(code=ErrorCode.JOIN_FAN_OUT.value, node="executor", sub_query_id="sq1",
                         message="The join repeats rows.")
    other_step = RunWarning(code=ErrorCode.JOIN_FAN_OUT.value, node="executor", sub_query_id="sq2", message="x")
    retried = RunWarning(code=ErrorCode.MISSING_SQL.value, node="generator", sub_query_id="sq1", message="y",
                         detail={"recovered": True})
    state = {"subgraph_outputs": {"sg1": _output(plan=_country_plan("Brazil"))},
             "warnings": [_retry_warning("Brasil", ["Brazil", "Brunei"]), fan_out, other_step, retried]}
    step = result_from_state(state).explanation.steps[0]
    # Said in the reader's words, from the final plan's literals; nothing of the planner's prompt.
    assert step.assumptions == ["'Brasil' is not in Customer country; 'Brazil' was used instead.",
                                "The join repeats rows."]

    # The replan did not use a suggested value: nothing is claimed about what was used.
    state = {"subgraph_outputs": {"sg1": _output(plan=_country_plan("Chile"))},
             "warnings": [_retry_warning("Brasil", ["Brazil"])]}
    assert result_from_state(state).explanation.steps[0].assumptions == [
        "'Brasil' is not in Customer country; the question was planned again."]


def test_planner_note_is_labelled():
    step = explain_plan(REVENUE_BY_COUNTRY, datasource_id="chinook")
    assert step.planner_note == "Revenue is unit price times quantity."
    # Model text appears only in its labelled field.
    others = step.model_dump(exclude={"planner_note"})
    assert "unit price times quantity." not in json.dumps(others)


def test_unaliased_column_matches_no_term_across_tables():
    plan = PlanModel.model_validate({
        "tables": [{"name": "Track", "alias": "t"}, {"name": "Album", "alias": "a"}],
        "joins": [{"left_alias": "t", "right_alias": "a", "condition": eq(col("t", "AlbumId"), col("a", "AlbumId"))}],
        "select_items": [{"expr": {"kind": "column", "column_name": "Title"}}],
    })
    used = KnowledgeUsed(datasource_id="chinook", digest="k1", terms=[
        TermMatch(term="album name", columns=["Album.Title"], source="yaml", score=1.0)])
    assert explain_plan(plan, datasource_id="chinook", knowledge_used=used).terms == []


# ---------------------------------------------------------------- what is never explained

def test_refused_plan_is_not_explained():
    """A plan the validator refused (refusal_names_tables off) must not confirm the table it named."""
    refusal = PipelineError(node="logical_validator", message="You do not have permission to read this data.",
                            severity=ErrorSeverity.ERROR, error_code=ErrorCode.SECURITY_VIOLATION)
    secret = PlanModel.model_validate({"tables": [{"name": "EmployeeSalary", "alias": "s"}],
                                       "select_items": [{"expr": col("s", "Amount")}]})
    refused = _output(plan=secret).model_copy(update={"status": "error", "errors": [refusal]})
    result = result_from_state({"subgraph_outputs": {"sg1": refused}, "errors": [refusal]})
    assert result.explanation is None
    assert "EmployeeSalary" not in json.dumps(result.model_dump(mode="json", exclude={"sub_queries"}))


def test_denied_or_dropped_step_is_not_explained():
    denial = PipelineError(node="ast_planner", message="Denied by a hook.", severity=ErrorSeverity.ERROR,
                           error_code=ErrorCode.HOOK_DENIED)
    dropped = _output().model_copy(update={"status": "dropped", "errors": [denial]})
    assert result_from_state({"subgraph_outputs": {"sg1": dropped}}).explanation is None
    # A run a hook ended withholds its data, and its explanation with it.
    assert result_from_state({"subgraph_outputs": {"sg1": _output()}, "errors": [denial]}).explanation is None


def test_unknown_operator_never_fails_the_result():
    names = phrases.Names({"g": "Genre"})
    rejected = Expr.model_validate({"kind": "binary", "op": "NOT", "left": col("g", "Name"), "right": lit("x")})
    assert phrases.phrase(rejected, names) == "Genre name NOT 'x'"
    plan = PlanModel.model_validate({"tables": [{"name": "Genre", "alias": "g"}],
                                     "select_items": [{"expr": col("g", "Name")}],
                                     "where": rejected.model_dump()})
    assert result_from_state({"subgraph_outputs": {"sg1": _output(plan=plan)}}).explanation is not None


def test_unrenderable_plan_gives_no_explanation():
    """A stored plan that no longer validates (a trace from an older version) leaves the explanation out."""
    output = _output().model_dump()
    output["plan"] = {"tables": [{"name": "Genre"}], "no_such_field": 1}
    result = result_from_state({"subgraph_outputs": {"sg1": output}})
    assert result.explanation is None and result.status == "plan_only"


# ---------------------------------------------------------------- assembly

def test_result_carries_the_explanation_for_a_plan_only_run():
    result = result_from_state({"subgraph_outputs": {"sg1": _output()}})
    assert result.status == "plan_only"
    assert result.explanation.steps == [explain_plan(REVENUE_BY_COUNTRY, datasource_id="chinook",
                                                     sub_query_id="sq1")]
    assert result.explanation.combined is None


def test_no_plan_no_explanation():
    assert result_from_state({}).explanation is None
    assert result_from_state({"subgraph_outputs": {"sg1": _output(plan=None)}}).explanation is None


def test_steps_follow_the_decomposition_and_say_how_they_combine():
    from nl2sql.pipeline.nodes.decomposer.schemas import CombineGroup, CombineInput, DecomposerResponse, JoinKeyPair

    a = SubQuery(id="a", datasource_id="chinook", intent="revenue by country")
    b = SubQuery(id="b", datasource_id="chinook", intent="customers by country")
    outputs = {"sg_b": _output().model_copy(update={"subgraph_id": "sg_b", "sub_query": b}),
               "sg_a": _output().model_copy(update={"subgraph_id": "sg_a", "sub_query": a})}
    # Built without the decomposer's checks: the explanation reads only the groups.
    decomposition = DecomposerResponse.model_construct(sub_queries=[a, b], combine_groups=[CombineGroup(
        group_id="g", operation="join", inputs=[CombineInput(subquery_id="a", role="left"),
                                                CombineInput(subquery_id="b", role="right")],
        join_keys=[JoinKeyPair(left="country", right="country")])])
    explanation = explain_state({"subgraph_outputs": outputs, "decomposer_response": decomposition})
    assert [s.sub_query_id for s in explanation.steps] == ["a", "b"]
    assert explanation.combined == "The results of a and b were joined on country."


def test_explanation_makes_no_model_call():
    """The renderer imports no model client and takes none: it cannot call a model."""
    package = Path(phrases.__file__).parent
    allowed = ("__future__", "json", "re", "typing", "pydantic", "nl2sql_sdk.knowledge", "nl2sql.explain",
               "nl2sql.knowledge.models", "nl2sql.pipeline.nodes.ast_planner")
    for path in package.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [node.module or ""] if isinstance(node, ast.ImportFrom) else [a.name for a in node.names]
                for name in names:
                    assert name.startswith(allowed), f"{path.name} imports {name}"
    # Assembly needs no runtime at all: there is nothing to call a model with.
    assert result_from_state({"subgraph_outputs": {"sg1": _output()}}).explanation is not None
