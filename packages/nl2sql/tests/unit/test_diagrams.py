"""Mermaid diagrams of an answer and of a schema, and the ``diagram`` chat tool (wave_c.md, section 4; row K2).

The answer view is a pure function of a ``QueryResult`` (its plans, never its rows); the schema view is read through
``datasources.schema``, so the caller's scope decides which tables it shows. No model is called anywhere.
"""
from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

import nl2sql
from nl2sql import QueryResult, UserContext
from nl2sql.api.query_api import RowSample, SubQueryResult
from nl2sql.charts.diagram import schema_diagram
from nl2sql.chat_tools import ToolContext, ToolNotApplicable
from nl2sql.chat_tools.diagram import DiagramArgs, DiagramTool
from nl2sql.conversations.models import Conversation, ToolCall, ToolStep, Turn
from nl2sql.pipeline.nodes.decomposer.schemas import (CombineGroup, CombineInput, DecomposerResponse, ExpectedColumn,
                                                      JoinKeyPair, SubQuery)
from nl2sql_sdk.knowledge import DatasourceKnowledge, JoinKnowledge

# The engine over a fake context, with a real index store (the helpers of the schema tests).
from test_public_facade_schema_index import ADMIN, _Context, _engine, _snapshot  # noqa: F401

VIEWER = UserContext(roles=["viewer"])  # reads chinook but not Artist
DIRECTIVES = ("%%{", "style ", "classDef", "linkStyle", "click ")


def _column(alias: str, name: str) -> Dict[str, Any]:
    return {"kind": "column", "alias": alias, "column_name": name}


def _join(left: str, right: str, *pairs) -> Dict[str, Any]:
    condition = None
    for a, b in pairs:
        part = {"kind": "binary", "op": "=", "left": _column(left, a), "right": _column(right, b)}
        condition = part if condition is None else {"kind": "binary", "op": "AND", "left": condition, "right": part}
    return {"left_alias": left, "right_alias": right, "join_type": "inner", "condition": condition}


def _plan(tables: List[tuple], joins: List[Dict[str, Any]] = ()) -> Dict[str, Any]:
    return {"tables": [{"name": n, "schema_name": s, "alias": a} for a, s, n in tables], "joins": list(joins)}


def _sub(sid: str, datasource: str, plan=None, sql: str = "", rows=None) -> SubQueryResult:
    return SubQueryResult(id=sid, datasource_id=datasource, plan=plan, sql=sql, rows=rows)


def _lines(text: str) -> List[str]:
    return [line.strip() for line in text.splitlines()]


def _no_directives(text: str) -> None:
    assert "%%{" not in text
    for line in _lines(text):
        assert not line.startswith(DIRECTIVES[1:]), line


# ---------------------------------------------------------------- the answer view

def test_diagram_for_is_exported_from_nl2sql():
    assert nl2sql.diagram_for is not None and "diagram_for" in nl2sql.__all__


def test_the_answer_view_draws_the_tables_and_the_join_columns():
    plan = _plan([("o", "public", "orders"), ("c", "public", "customers")],
                 [_join("o", "c", ("customer_id", "id"))])
    text = nl2sql.diagram_for(QueryResult(sub_queries=[_sub("sq1", "shop", plan)]))

    assert text.splitlines()[0] == "flowchart LR"
    assert "public.orders" in text and "public.customers" in text
    assert "customer_id = id" in text
    assert "subgraph" not in text  # one datasource: no grouping
    _no_directives(text)


def test_a_join_on_two_columns_labels_both():
    plan = _plan([("a", "s", "a"), ("b", "s", "b")], [_join("a", "b", ("x", "x"), ("y", "z"))])
    text = nl2sql.diagram_for(QueryResult(sub_queries=[_sub("sq1", "d", plan)]))

    assert "x = x, y = z" in text


def test_a_table_read_twice_is_one_node():
    plan = _plan([("a", None, "employees"), ("b", None, "employees")], [_join("a", "b", ("manager_id", "id"))])
    text = nl2sql.diagram_for(QueryResult(sub_queries=[_sub("sq1", "hr", plan)]))

    assert text.count('"employees"') == 1


def test_several_datasources_get_a_subgraph_each_and_the_combine_keys_between_them():
    one = _plan([("o", None, "orders")])
    two = _plan([("c", None, "customers")])
    decomposition = DecomposerResponse(
        sub_queries=[SubQuery(id="sq1", datasource_id="sales", intent="orders",
                              expected_schema=[ExpectedColumn(name="customer_id", dtype="int")]),
                     SubQuery(id="sq2", datasource_id="crm", intent="customers",
                              expected_schema=[ExpectedColumn(name="id", dtype="int")])],
        combine_groups=[CombineGroup(group_id="g1", operation="join",
                                     inputs=[CombineInput(subquery_id="sq1", role="left"),
                                             CombineInput(subquery_id="sq2", role="right")],
                                     join_keys=[JoinKeyPair(left="customer_id", right="id")])])
    result = QueryResult(sub_queries=[_sub("sq1", "sales", one), _sub("sq2", "crm", two)], decomposition=decomposition)

    text = nl2sql.diagram_for(result)

    assert text.count("subgraph") == 2 and '"sales"' in text and '"crm"' in text
    assert "customer_id = id" in text
    _no_directives(text)


def test_a_verified_answer_lists_the_tables_its_sql_reads_unconnected():
    sub = _sub("sq1", "shop", plan=None, sql="SELECT c.name FROM public.customers c JOIN orders o ON o.cid = c.id")
    text = nl2sql.diagram_for(QueryResult(sub_queries=[sub]))

    assert "public.customers" in text and "orders" in text
    assert "---" not in text and "-->" not in text


def test_an_answer_with_no_tables_still_draws_something():
    text = nl2sql.diagram_for(QueryResult())

    assert text.startswith("flowchart LR") and "No tables" in text


def test_the_answer_view_reads_the_plan_and_never_the_rows():
    plan = _plan([("o", None, "orders")])
    rows = RowSample(columns=["secret"], rows=[["hunter2-the-password"]], total_rows=1)
    text = nl2sql.diagram_for(QueryResult(sub_queries=[_sub("sq1", "shop", plan, rows=rows)]))

    assert "hunter2" not in text and "secret" not in text


def test_no_colour_and_no_init_line():
    plan = _plan([("o", None, "orders"), ("c", None, "customers")], [_join("o", "c", ("a", "b"))])
    text = nl2sql.diagram_for(QueryResult(sub_queries=[_sub("sq1", "shop", plan)]))

    assert "%%" not in text and "#" not in text and "fill" not in text and "stroke" not in text


HOSTILE = ['x"]\n  click a call alert(1)\n  style a fill:#f00', "%%{init: {'theme':'dark'}}%%", 'a" --> b["c',
           "classDef bad fill:red", "linkStyle 0 stroke:red", "<script>alert(1)</script>", "Robert'); DROP--"]


@pytest.mark.parametrize("name", HOSTILE)
def test_hostile_names_cannot_inject_a_directive_into_the_answer_view(name):
    plan = _plan([("a", name, name), ("b", None, "ok")], [_join("a", "b", (name, name))])
    sub = _sub("sq1", name, plan)
    text = nl2sql.diagram_for(QueryResult(sub_queries=[sub, _sub("sq2", "other", plan)]))

    for line in _lines(text):
        assert not line.startswith(("click", "style", "classDef", "linkStyle", "%%")), line
    assert "%%{" not in text and "<" not in text
    assert text.count("\n") < 40  # a newline in a name did not add lines


# ---------------------------------------------------------------- the schema view

def test_the_schema_view_is_an_er_diagram_of_keys_and_relationships(tmp_path):
    text = _engine(_Context(tmp_path, snapshot=_snapshot())).datasources.diagram("chinook", user_context=ADMIN)

    assert text.splitlines()[0] == "erDiagram"
    assert re.search(r"INTEGER AlbumId PK", text) and re.search(r"INTEGER ArtistId FK", text)
    assert re.search(r'Artist \|\|--o\{ Album : "ArtistId = ArtistId"', text)
    _no_directives(text)


def test_the_schema_view_requires_a_caller(tmp_path):
    with pytest.raises(TypeError):
        _engine(_Context(tmp_path, snapshot=_snapshot())).datasources.diagram("chinook")


def test_a_denied_table_and_its_relationships_are_not_drawn(tmp_path):
    text = _engine(_Context(tmp_path, snapshot=_snapshot())).datasources.diagram("chinook", user_context=VIEWER)

    assert "Album" in text and "Artist" not in text


def test_a_caller_with_no_role_sees_an_empty_diagram(tmp_path):
    text = _engine(_Context(tmp_path, snapshot=_snapshot())).datasources.diagram("chinook", user_context=UserContext())

    assert "Album" not in text and "Artist" not in text and text.startswith("erDiagram")


def test_a_glossary_join_is_a_relationship_too(tmp_path):
    ctx = _Context(tmp_path, snapshot=_snapshot())
    ctx.knowledge = SimpleNamespace(get=lambda _ds: DatasourceKnowledge(
        joins=[JoinKnowledge(**{"from": "Album.AlbumId", "to": "Artist.ArtistId", "cardinality": "many-to-one"})]))

    text = _engine(ctx).datasources.diagram("chinook", user_context=ADMIN)

    assert 'Album }o--|| Artist : "AlbumId = ArtistId"' in text


def test_a_glossary_join_to_a_denied_table_is_not_drawn(tmp_path):
    ctx = _Context(tmp_path, snapshot=_snapshot())
    ctx.knowledge = SimpleNamespace(get=lambda _ds: DatasourceKnowledge(
        joins=[JoinKnowledge(**{"from": "Album.AlbumId", "to": "Artist.ArtistId"})]))

    text = _engine(ctx).datasources.diagram("chinook", user_context=VIEWER)

    assert "Artist" not in text


def test_tables_that_match_nothing_the_caller_may_see_is_an_error(tmp_path):
    engine = _engine(_Context(tmp_path, snapshot=_snapshot()))

    with pytest.raises(ValueError, match="No table"):
        engine.datasources.diagram("chinook", user_context=VIEWER, tables=["Artist"])


def test_tables_picks_those_and_their_neighbours():
    view = {"datasource_id": "d", "tables": [_table("a", fk="b"), _table("b"), _table("c", fk="d"), _table("d")]}

    text = schema_diagram(view, tables=["a"])

    assert "a {" in text and "b {" in text and "c {" not in text and "d {" not in text


def test_more_than_max_tables_is_cut_to_those_with_the_most_relationships_and_says_so():
    tables = [_table(f"t{i}") for i in range(6)]
    tables[3]["foreign_keys"] = [{"columns": ["id"], "references_table": "t4", "references_columns": ["id"]},
                                 {"columns": ["id"], "references_table": "t5", "references_columns": ["id"]}]

    text = schema_diagram({"datasource_id": "d", "tables": tables}, max_tables=3)

    assert text.count(" {") == 3 and "t3 {" in text
    assert "%% 3 more tables not shown" in text


def test_a_cut_comment_names_no_table():
    tables = [_table(f"secret{i}") for i in range(5)]

    text = schema_diagram({"datasource_id": "d", "tables": tables}, max_tables=2)

    assert [line for line in _lines(text) if line.startswith("%%")] == ["%% 3 more tables not shown"]


@pytest.mark.parametrize("name", HOSTILE)
def test_hostile_names_cannot_inject_a_directive_into_the_schema_view(name):
    hostile = _table(name, column=name, kind=name, fk=name)
    view = {"datasource_id": name, "tables": [hostile, _table("ok", fk=name)]}

    text = schema_diagram(view)

    for line in _lines(text):
        assert not line.startswith(("click", "style", "classDef", "linkStyle", "%%")), line
    assert "%%{" not in text and "<" not in text and text.count("\n") < 40


def _table(name: str, *, column: str = "id", kind: str = "INTEGER", fk: str = "") -> Dict[str, Any]:
    keys = [{"columns": [column], "references_table": fk, "references_columns": [column]}] if fk else []
    return {"name": name, "schema": "main", "row_count": None, "description": "",
            "columns": [{"name": column, "type": kind, "nullable": False, "primary_key": True, "description": ""}],
            "foreign_keys": keys}


# ---------------------------------------------------------------- the chat tool

def _conversation(*turns: Turn) -> Conversation:
    return Conversation.model_construct(id="c1", title="t", turns=list(turns))


def _ask_turn(index: int, result: QueryResult) -> Turn:
    step = ToolStep(call=ToolCall(name="ask", args={"question": "q"}), status="ok", question="q", result=result)
    return Turn.model_construct(index=index, steps=[step])


class _Datasources:
    def __init__(self):
        self.calls: List[tuple] = []

    def diagram(self, datasource_id, *, user_context, tables=None):
        self.calls.append((datasource_id, user_context, tables))
        return "erDiagram\n    a {\n    }"


def _context(conversation, user=ADMIN):
    datasources = _Datasources()
    return ToolContext(conversation=conversation, user=user, engine=SimpleNamespace(datasources=datasources)), datasources


def test_the_tool_draws_the_latest_answer_by_default():
    plan = _plan([("o", None, "orders")])
    conversation = _conversation(_ask_turn(1, QueryResult(sub_queries=[_sub("sq1", "shop", plan)])))
    ctx, _ = _context(conversation)

    out = DiagramTool()(ctx, DiagramArgs())

    assert out.kind == "mermaid" and out.body.startswith("flowchart LR") and "orders" in out.body


def test_the_tool_picks_a_turn_and_a_step():
    first = _ask_turn(1, QueryResult(sub_queries=[_sub("sq1", "shop", _plan([("o", None, "orders")]))]))
    second = _ask_turn(2, QueryResult(sub_queries=[_sub("sq1", "shop", _plan([("c", None, "customers")]))]))
    ctx, _ = _context(_conversation(first, second))

    assert "orders" in DiagramTool()(ctx, DiagramArgs(turn=1)).body
    assert "customers" in DiagramTool()(ctx, DiagramArgs()).body


def test_the_tool_has_nothing_to_draw_without_an_answer():
    ctx, _ = _context(_conversation())

    with pytest.raises(ToolNotApplicable):
        DiagramTool()(ctx, DiagramArgs())
    with pytest.raises(ToolNotApplicable):
        DiagramTool()(ctx, DiagramArgs(turn=9))


def test_the_schema_view_passes_the_conversations_caller():
    ctx, datasources = _context(_conversation(), user=VIEWER)

    out = DiagramTool()(ctx, DiagramArgs(view="schema", datasource_id="chinook", tables=["Album"]))

    assert out.kind == "mermaid" and out.body.startswith("erDiagram")
    assert datasources.calls == [("chinook", VIEWER, ["Album"])]


def test_the_schema_view_needs_a_datasource():
    ctx, _ = _context(_conversation())

    with pytest.raises(ToolNotApplicable):
        DiagramTool()(ctx, DiagramArgs(view="schema"))


def test_an_unknown_datasource_is_not_applicable():
    ctx, datasources = _context(_conversation())
    datasources.diagram = lambda *a, **k: (_ for _ in ()).throw(ValueError("Unknown datasource ID: nope"))

    with pytest.raises(ToolNotApplicable, match="Unknown datasource"):
        DiagramTool()(ctx, DiagramArgs(view="schema", datasource_id="nope"))
