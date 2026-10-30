"""The planner is told the allowed joins up front (wave_c.md, section 18.13, C-LT4).

One [ALLOWED_JOINS] line per declared join between two tables it was sent, derived by the same helper the
logical validator's join hint uses, so the prompt and the validator cannot disagree.
"""
from unittest.mock import MagicMock

from nl2sql.pipeline.nodes.ast_planner.node import ASTPlannerNode
from nl2sql.pipeline.nodes.ast_planner.schemas import PlannerAnswer, PlanModel
from nl2sql.pipeline.nodes.ast_planner.prompts import PLANNER_PROMPT, PLANNER_SYSTEM_PROMPT
from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery
from nl2sql.pipeline.nodes.schema_retriever.schema import Column, Table, render_schema_for_prompt
from nl2sql.pipeline.nodes.validator.join_hints import allowed_joins, join_hint, normalize_name
from nl2sql.pipeline.state import SubgraphExecutionState
from nl2sql.testing.runtime import fake_runtime


def _rel(from_table, from_columns, to_table, to_columns=None):
    return {"from_table": from_table, "to_table": to_table,
            "from_columns": from_columns, "to_columns": to_columns or from_columns}


def _table(name, columns, *relationships):
    return Table(name=name, columns=[Column(name=c, type="int") for c in columns], relationships=list(relationships))


def _lines(relationships, sent):
    return allowed_joins(relationships, normalize_name, sent)


def test_only_joins_between_two_sent_tables_are_listed():
    rels = [_rel("public.inventory", ["film_id"], "public.film"),
            _rel("public.inventory", ["store_id"], "public.store")]

    assert _lines(rels, {"inventory", "film"}) == ["film.film_id = inventory.film_id"]


def test_both_directions_of_one_foreign_key_collapse_to_one_line():
    rels = [_rel("a", ["x"], "b", ["y"]), _rel("b", ["y"], "a", ["x"])]

    assert _lines(rels, {"a", "b"}) == ["a.x = b.y"]


def test_lines_are_sorted_and_deduplicated_whatever_the_input_order():
    rels = [_rel("rental", ["inventory_id"], "inventory"), _rel("inventory", ["film_id"], "film"),
            _rel("rental", ["inventory_id"], "inventory")]
    sent = {"rental", "inventory", "film"}

    assert _lines(rels, sent) == ["film.film_id = inventory.film_id", "inventory.inventory_id = rental.inventory_id"]
    assert _lines(list(reversed(rels)), sent) == _lines(rels, sent)


def test_a_multi_column_key_is_one_line_joined_with_and():
    rels = [_rel("line", ["order_id", "region"], "orders", ["id", "region_code"])]

    assert _lines(rels, {"line", "orders"}) == ["line.order_id = orders.id AND line.region = orders.region_code"]


def test_names_are_the_bare_names_the_plan_uses():
    rels = [_rel("[main].[Album]", ["ArtistId"], "[main].[Artist]")]

    assert _lines(rels, {"album", "artist"}) == ["album.artistid = artist.artistid"]


def test_the_validator_hint_names_the_same_joins_as_the_prompt_lines():
    rels = [_rel("inventory", ["film_id"], "film")]
    sent = {"inventory", "film"}

    [line] = _lines(rels, sent)

    assert line == "film.film_id = inventory.film_id"
    assert "inventory.film_id = film.film_id" in join_hint(rels, "inventory", "film", normalize_name, sent)


def _planner_inputs(tables):
    llm = MagicMock()
    llm.with_structured_output.return_value = llm
    ctx = fake_runtime(llm_registry=MagicMock(), state=None)
    ctx.llm_registry.get_llm.return_value = llm
    ctx.llm_registry.effective.return_value = None
    node = ASTPlannerNode(ctx)
    node.chain = MagicMock()
    node.chain.invoke.return_value = PlannerAnswer(plan=PlanModel(tables=[], select_items=[]))
    node(SubgraphExecutionState(trace_id="t", sub_query=SubQuery(id="sq1", datasource_id="d", intent="q"),
                                relevant_tables=tables))
    return node.chain.invoke.call_args.args[0]


TABLES = [
    _table("inventory", ["inventory_id", "film_id"], _rel("public.inventory", ["film_id"], "public.film"),
           _rel("public.inventory", ["store_id"], "public.store")),
    _table("film", ["film_id"]),
]


def test_the_planner_prompt_lists_the_allowed_joins_right_after_the_schema_in_the_system_message():
    values = _planner_inputs(TABLES)
    system, human = PLANNER_PROMPT.format_messages(**values)

    schema = render_schema_for_prompt(TABLES)
    assert system.content.endswith(f"{schema}\n\n[ALLOWED_JOINS]\nfilm.film_id = inventory.film_id")
    assert "[ALLOWED_JOINS]" not in human.content


def test_with_no_declared_join_the_block_says_none():
    values = _planner_inputs([_table("film", ["film_id"])])
    system, _ = PLANNER_PROMPT.format_messages(**values)

    assert system.content.endswith("[ALLOWED_JOINS]\nnone")


def test_the_block_is_byte_stable_across_renders():
    first = PLANNER_PROMPT.format_messages(**_planner_inputs(TABLES))[0].content
    second = PLANNER_PROMPT.format_messages(**_planner_inputs(list(reversed(TABLES))))[0].content

    assert first == second


def test_the_join_instructions_point_at_the_allowed_joins_block():
    assert "[ALLOWED_JOINS]" in PLANNER_SYSTEM_PROMPT
    assert "share a column name" in PLANNER_SYSTEM_PROMPT
    assert "bridge" in PLANNER_SYSTEM_PROMPT
    assert "use relationships listed in [RELEVANT_TABLES]" not in PLANNER_SYSTEM_PROMPT
    assert "Joins MUST use relationships provided in [RELEVANT_TABLES]" not in PLANNER_SYSTEM_PROMPT

