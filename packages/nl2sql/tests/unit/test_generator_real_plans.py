"""The generator against a plan a real model produced, run on real Chinook.

``fixtures/gpt4o_genre_sales_plan.json`` is the plan gpt-4o returned for
"Which genre sells the most tracks?" in a live run, copied verbatim. The
logical validator passed it, correctly: it is a valid plan. The generator
turned it into

    SELECT t1.Name AS genre, SUM(*(t3.UnitPrice, t3.Quantity)) AS track_sales
    FROM Genre AS t1
    INNER JOIN Genre AS t1 ON t2.GenreId = t1.GenreId
    INNER JOIN Track AS t2 ON t3.TrackId = t2.TrackId
    GROUP BY t1.Name LIMIT 1000

which SQLite rejects. Two separate defects: each join added its
``right_alias`` table whether or not that table was already in scope, and
arithmetic operators fell through to ``exp.Anonymous`` named after the
operator. Every hand-written recording happened to use the shapes the code
already handled, so these tests replay what a model actually wrote.
"""

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlglot import expressions as exp

import nl2sql
from nl2sql.pipeline.nodes.ast_planner.schemas import (
    ASTPlannerResponse,
    Expr,
    JoinSpec,
    PlanModel,
    SelectItem,
    TableRef,
)
from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery
from nl2sql.pipeline.nodes.generator.node import GeneratorNode, SqlVisitor
from nl2sql.pipeline.state import SubgraphExecutionState

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "gpt4o_genre_sales_plan.json"
CHINOOK = Path(nl2sql.__file__).resolve().parent / "cli" / "demo" / "data" / "chinook.sqlite"


def _captured_plan() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _generate(plan: PlanModel) -> dict:
    adapter = SimpleNamespace(row_limit=1000, get_dialect=lambda: "sqlite")
    ctx = SimpleNamespace(ds_registry=SimpleNamespace(get_adapter=lambda _id: adapter))
    state = SubgraphExecutionState(
        trace_id="t",
        sub_query=SubQuery(id="sq1", datasource_id="chinook", intent="q"),
        ast_planner_response=ASTPlannerResponse(plan=plan),
    )
    return GeneratorNode(ctx)(state)


def _sql(plan: PlanModel) -> str:
    result = _generate(plan)
    assert not result.get("errors"), result.get("errors")
    return result["generator_response"].sql_draft


def _run_on_chinook(sql: str) -> list:
    conn = sqlite3.connect(f"file:{CHINOOK.as_posix()}?mode=ro", uri=True)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def _top_row(rows: list) -> tuple:
    return max(rows, key=lambda r: r[1])


# --- the captured plan, end to end on the vendored database -----------------


def test_captured_plan_generates_sql_that_runs_on_chinook():
    sql = _sql(PlanModel.model_validate(_captured_plan()))

    rows = _run_on_chinook(sql)

    assert len(rows) == 24  # every genre that has ever been sold
    genre, revenue = _top_row(rows)
    assert genre == "Rock"
    assert revenue == pytest.approx(826.65)


def test_captured_plan_puts_each_table_in_scope_exactly_once():
    sql = _sql(PlanModel.model_validate(_captured_plan()))

    tree = exp.maybe_parse(sql, dialect="sqlite")
    in_scope = [t.alias_or_name for t in tree.find_all(exp.Table)]
    assert sorted(in_scope) == ["t1", "t2", "t3"]
    names = {t.alias_or_name: t.name for t in tree.find_all(exp.Table)}
    assert names == {"t1": "Genre", "t2": "Track", "t3": "InvoiceLine"}


# --- defect 1: join direction is not attachment order -----------------------


def _swap_sides(join: dict) -> dict:
    swapped = dict(join)
    swapped["left_alias"], swapped["right_alias"] = join["right_alias"], join["left_alias"]
    return swapped


def test_join_attaches_whichever_side_is_not_yet_in_scope():
    """The same plan with every join written the other way round runs the same."""
    raw = _captured_plan()
    raw["joins"] = [_swap_sides(j) for j in raw["joins"]]

    rows = _run_on_chinook(_sql(PlanModel.model_validate(raw)))

    assert _top_row(rows) == ("Rock", pytest.approx(826.65))


def test_joins_listed_out_of_dependency_order_are_attached_once_reachable():
    """``InvoiceLine``-``Track`` first: neither side is in scope yet when it is read."""
    raw = _captured_plan()
    raw["joins"] = list(reversed(raw["joins"]))

    rows = _run_on_chinook(_sql(PlanModel.model_validate(raw)))

    assert _top_row(rows) == ("Rock", pytest.approx(826.65))


def test_a_table_that_no_join_reaches_is_a_planning_error():
    raw = _captured_plan()
    raw["joins"] = raw["joins"][:1]  # InvoiceLine (t3) is referenced but never joined

    result = _generate(PlanModel.model_validate(raw))

    assert result["errors"]
    assert "t3" in result["errors"][0].message
    assert result["generator_response"].sql_draft is None


def test_a_join_between_two_tables_already_in_scope_is_a_planning_error():
    raw = _captured_plan()
    raw["joins"].append(dict(raw["joins"][0], ordinal=2))

    result = _generate(PlanModel.model_validate(raw))

    assert result["errors"]
    assert "already" in result["errors"][0].message


def test_a_join_naming_an_undeclared_alias_is_a_planning_error():
    raw = _captured_plan()
    raw["joins"][1] = dict(raw["joins"][1], left_alias="t9")

    result = _generate(PlanModel.model_validate(raw))

    assert result["errors"]
    assert "t9" in result["errors"][0].message


def test_joined_tables_keep_their_schema_qualifier():
    plan = PlanModel(
        tables=[
            TableRef(name="Album", schema_name="main", alias="al", ordinal=0),
            TableRef(name="Artist", schema_name="main", alias="ar", ordinal=1),
        ],
        joins=[
            JoinSpec(
                left_alias="ar",
                right_alias="al",
                ordinal=0,
                condition=Expr(
                    kind="binary",
                    op="=",
                    left=Expr(kind="column", alias="ar", column_name="ArtistId"),
                    right=Expr(kind="column", alias="al", column_name="ArtistId"),
                ),
            )
        ],
        select_items=[SelectItem(ordinal=0, expr=Expr(kind="column", alias="ar", column_name="Name"))],
    )

    sql = _sql(plan)

    assert "JOIN main.Artist AS ar" in sql
    assert _run_on_chinook(sql)


def test_an_outer_join_attached_from_its_left_side_keeps_the_preserved_table():
    """``Artist LEFT JOIN Album`` with ``Album`` first in the FROM clause.

    Attaching ``Artist`` to ``Album`` must become a RIGHT JOIN, or the artists
    with no albums -- the rows the outer join exists to keep -- disappear.
    """
    plan = PlanModel(
        tables=[
            TableRef(name="Album", alias="al", ordinal=0),
            TableRef(name="Artist", alias="ar", ordinal=1),
        ],
        joins=[
            JoinSpec(
                left_alias="ar",
                right_alias="al",
                join_type="left",
                ordinal=0,
                condition=Expr(
                    kind="binary",
                    op="=",
                    left=Expr(kind="column", alias="ar", column_name="ArtistId"),
                    right=Expr(kind="column", alias="al", column_name="ArtistId"),
                ),
            )
        ],
        select_items=[SelectItem(ordinal=0, expr=Expr(kind="column", alias="ar", column_name="ArtistId"))],
    )

    rows = _run_on_chinook(_sql(plan))

    assert len({r[0] for r in rows}) == 275  # every artist, with or without albums


# --- defect 2: arithmetic renders as arithmetic, wherever it is nested -------


def _col(alias: str, name: str) -> Expr:
    return Expr(kind="column", alias=alias, column_name=name)


def _num(value) -> Expr:
    return Expr(kind="literal", value=value)


@pytest.mark.parametrize(
    "op, node, expected",
    [
        ("+", exp.Add, 9),
        ("-", exp.Sub, 5),
        ("*", exp.Mul, 14),
        # True division, sqlglot's portable meaning of ``/``: SQLite's native
        # integer division would silently truncate every ratio and average.
        ("/", exp.Div, 3.5),
        ("%", exp.Mod, 1),
    ],
)
def test_every_arithmetic_operator_renders_as_sql_arithmetic(op, node, expected):
    binary = Expr(kind="binary", op=op, left=_num(7), right=_num(2))

    top_level = SqlVisitor().visit(binary)
    in_a_function = SqlVisitor().visit(Expr(kind="func", func_name="SUM", args=[binary]))

    assert isinstance(top_level, node)
    assert isinstance(in_a_function.expressions[0], node)
    assert _run_on_chinook(f"SELECT {top_level.sql(dialect='sqlite')}") == [(expected,)]


def test_nested_arithmetic_keeps_its_grouping():
    # (7 + 2) * 3 = 27; rendered without parentheses it would be 7 + 6 = 13.
    add = Expr(kind="binary", op="+", left=_num(7), right=_num(2))
    mul = Expr(kind="binary", op="*", left=add, right=_num(3))

    sql = SqlVisitor().visit(mul).sql(dialect="sqlite")

    assert _run_on_chinook(f"SELECT {sql}") == [(27,)]


def test_right_nested_subtraction_keeps_its_grouping():
    # 10 - (4 - 1) = 7; left-associated it would be 5.
    inner = Expr(kind="binary", op="-", left=_num(4), right=_num(1))
    outer = Expr(kind="binary", op="-", left=_num(10), right=inner)

    sql = SqlVisitor().visit(outer).sql(dialect="sqlite")

    assert _run_on_chinook(f"SELECT {sql}") == [(7,)]


@pytest.mark.parametrize("op, expected", [("IS", [(1,)]), ("IS NOT", [(0,)])])
def test_is_and_is_not_render_as_null_tests(op, expected):
    binary = Expr(kind="binary", op=op, left=Expr(kind="literal", is_null=True), right=Expr(kind="literal", is_null=True))

    sql = SqlVisitor().visit(binary).sql(dialect="sqlite")

    assert _run_on_chinook(f"SELECT {sql}") == expected


def test_an_operator_the_visitor_cannot_render_is_an_error_not_a_function():
    binary = Expr(kind="binary", op="NOT", left=_num(1), right=_num(2))

    with pytest.raises(ValueError, match="NOT"):
        SqlVisitor().visit(binary)


def test_the_validator_builds_its_query_with_the_same_arithmetic():
    """``_build_validation_query`` renders through ``SqlVisitor`` too.

    It resolved the columns inside ``*(a, b)`` without complaint, because
    ``qualify()`` does not care what a function is called, so it passed the
    plan while holding the same broken tree the generator emitted.
    """
    from nl2sql.pipeline.nodes.validator.node import LogicalValidatorNode

    validator = LogicalValidatorNode(SimpleNamespace(ds_registry=SimpleNamespace(), rbac=None))
    plan = PlanModel.model_validate(_captured_plan())

    query = validator._build_validation_query(plan, ["t1", "t2", "t3"])

    assert query.find(exp.Mul) is not None
    assert not [a for a in query.find_all(exp.Anonymous) if a.name == "*"]
