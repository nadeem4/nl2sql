"""The schema retriever keeps what a join and an answer need, and survives a failed search (B103).

The 0.9.0 smoke test asked Pagila (29 tables) for "the five films in the
Sports category with the highest total rental revenue". The planning search
returned twelve columns across eight tables, none of them a key: ``film``
arrived without ``film_id`` or ``title``, ``category`` without ``name``, and no
join could be bound. A second run's embedding model failed to load, the
retriever returned no tables at all, and every retry planned blind.
"""
from types import SimpleNamespace

import pytest

from nl2sql.common.errors import ErrorCode
from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery
from nl2sql.pipeline.nodes.schema_retriever.node import SchemaRetrieverNode
from nl2sql.pipeline.state import SubgraphExecutionState
from nl2sql.policies import UserContext
from nl2sql.testing.runtime import fake_runtime
from nl2sql_sdk.schema import (
    ColumnContract,
    ForeignKeyContract,
    SchemaContract,
    SchemaMetadata,
    SchemaSnapshot,
    TableContract,
    TableMetadata,
    TableRef,
)

DS = "pagila"


def _ref(name: str) -> TableRef:
    return TableRef(schema_name="public", table_name=name)


FILM, FILM_CATEGORY, CATEGORY, LANGUAGE = (_ref(n) for n in ("film", "film_category", "category", "language"))


def _col(name: str, pk: bool = False, data_type: str = "int") -> ColumnContract:
    return ColumnContract(name=name, data_type=data_type, is_nullable=not pk, is_primary_key=pk)


def _fk(columns, table: TableRef, referred) -> ForeignKeyContract:
    return ForeignKeyContract(constrained_columns=columns, referred_table=table, referred_columns=referred)


def _pagila() -> SchemaSnapshot:
    """Four Pagila tables plus 25 filler tables: 29, too many to send whole."""
    contracts = {
        FILM: TableContract(table=FILM, columns={c.name: c for c in [
            _col("film_id", pk=True), _col("title", data_type="text"), _col("description", data_type="text"),
            _col("release_year"), _col("rental_rate", data_type="numeric"), _col("language_id")]},
            foreign_keys=[_fk(["language_id"], LANGUAGE, ["language_id"])]),
        FILM_CATEGORY: TableContract(table=FILM_CATEGORY, columns={c.name: c for c in [
            _col("film_id", pk=True), _col("category_id", pk=True), _col("last_update", data_type="timestamp")]},
            foreign_keys=[_fk(["film_id"], FILM, ["film_id"]), _fk(["category_id"], CATEGORY, ["category_id"])]),
        CATEGORY: TableContract(table=CATEGORY, columns={c.name: c for c in [
            _col("category_id", pk=True), _col("name", data_type="text"),
            _col("last_update", data_type="timestamp")]}, foreign_keys=[]),
        LANGUAGE: TableContract(table=LANGUAGE, columns={c.name: c for c in [
            _col("language_id", pk=True), _col("name", data_type="text")]}, foreign_keys=[]),
    }
    for i in range(25):
        ref = _ref(f"filler_{i}")
        contracts[ref] = TableContract(table=ref, columns={"id": _col("id", pk=True)}, foreign_keys=[])
    return SchemaSnapshot(
        contract=SchemaContract(datasource_id=DS, engine_type="postgresql",
                                tables={ref.full_name: c for ref, c in contracts.items()}),
        metadata=SchemaMetadata(datasource_id=DS, engine_type="postgresql",
                                tables={ref.full_name: TableMetadata(table=ref, row_count=1, columns={})
                                        for ref in contracts}),
    )


def _doc(**metadata):
    return SimpleNamespace(metadata=metadata, page_content="")


def _store(built, **overrides):
    """A vector store whose planning search returns only non-key columns, as the smoke test's did."""
    tables = [FILM, FILM_CATEGORY, CATEGORY]
    store = dict(
        snapshot=lambda _id: built,
        retrieve_schema_context=lambda *_a, **_k: [_doc(type="schema.table", table=t.full_name) for t in tables],
        retrieve_column_candidates=lambda *_a, **_k: [],
        retrieve_planning_context=lambda *_a, **_k: [
            _doc(type="schema.column", table=FILM.full_name, column="release_year"),
            _doc(type="schema.column", table=FILM.full_name, column="rental_rate"),
            _doc(type="schema.column", table=FILM_CATEGORY.full_name, column="last_update"),
            _doc(type="schema.column", table=CATEGORY.full_name, column="last_update"),
        ],
    )
    store.update(overrides)
    return SimpleNamespace(**store)


def _node(store, allow=("*",), snapshot=None, full_snapshot_max_tables=15):
    return SchemaRetrieverNode(fake_runtime(
        settings={"retrieval": {"full_snapshot_max_tables": full_snapshot_max_tables}},
        vector_store=store,
        state=SimpleNamespace(get_snapshot=lambda _id, _v: snapshot),
        policies={"roles": {"analyst": {"description": "d", "allow": list(allow)}}},
    ))


def _state():
    return SubgraphExecutionState(
        trace_id="t",
        sub_query=SubQuery(id="sq1", datasource_id=DS,
                           intent="the five films in the Sports category with the highest total rental revenue"),
        user_context=UserContext(roles=["analyst"]),
    )


def _columns(out):
    return {t.name: {c.name for c in t.columns} for t in out["relevant_tables"]}


def test_a_pruned_table_keeps_its_keys():
    out = _node(_store(_pagila()))(_state())

    columns = _columns(out)
    assert {"film_id", "language_id", "release_year", "rental_rate"} <= columns["film"]
    assert {"film_id", "category_id"} <= columns["film_category"]
    assert "category_id" in columns["category"]
    # Still pruned: a column nothing asked for and no join needs stays out.
    assert "description" not in columns["film"]


def test_every_join_offered_can_be_bound():
    out = _node(_store(_pagila()))(_state())

    columns = _columns(out)
    names = {t.name: t for t in out["relevant_tables"]}
    for table in out["relevant_tables"]:
        for rel in table.relationships:
            assert set(rel["from_columns"]) <= columns[table.name], rel
            to_name = rel["to_table"].split(".")[-1]
            if to_name in names:
                assert set(rel["to_columns"]) <= columns[to_name], rel


def test_a_pruned_table_keeps_its_label_column():
    # The question groups by the film's title and filters on the category's name; neither is a key.
    out = _node(_store(_pagila()))(_state())

    columns = _columns(out)
    assert "title" in columns["film"]
    assert "name" in columns["category"]


def _raises(*_a, **_k):
    raise RuntimeError("bad allocation")


def test_a_failed_search_sends_the_snapshot_with_a_warning():
    snapshot = _pagila()
    out = _node(_store(snapshot, retrieve_schema_context=_raises),
                allow=[f"{DS}.public.film", f"{DS}.public.category"], full_snapshot_max_tables=1)(_state())

    assert {t.name for t in out["relevant_tables"]} == {"film", "category"}
    # Whole tables, as the no-candidates fallback sends them.
    assert "description" in _columns(out)["film"]
    assert [w.code for w in out["warnings"]] == [ErrorCode.SCHEMA_RETRIEVAL_FAILED.value]
    assert "bad allocation" in out["warnings"][0].message
    assert not out.get("errors")
    # A hidden table never reaches the output.
    assert FILM_CATEGORY.full_name not in str(out) and LANGUAGE.full_name not in str(out)


def test_a_failed_search_without_a_snapshot_ends_the_run():
    # The index cannot even be read, so there is no snapshot to fall back to.
    out = _node(_store(None, snapshot=_raises))(_state())

    assert out["relevant_tables"] == []
    [error] = out["errors"]
    assert error.error_code == ErrorCode.SCHEMA_RETRIEVAL_FAILED
    assert not error.is_retryable


def test_the_sql_agent_ends_before_planning_when_retrieval_fails(monkeypatch):
    """No snapshot and a failed search: the planner is never called."""
    from nl2sql.pipeline.subgraphs import sql_agent

    planned = []
    for name in ("LogicalValidatorNode", "RefinerNode", "GeneratorNode", "ExecutorNode"):
        monkeypatch.setattr(sql_agent, name, lambda _ctx: (lambda state: {}))
    monkeypatch.setattr(sql_agent, "ASTPlannerNode", lambda _ctx: (lambda state: planned.append(state) or {}))
    store = _store(None, snapshot=_raises)
    ctx = fake_runtime(vector_store=store, state=SimpleNamespace(get_snapshot=lambda _id, _v: None),
                       policies={"allow_all": True})
    graph = sql_agent.build_sql_agent_graph(ctx)

    out = graph.invoke(_state())

    assert planned == []
    assert [e.error_code for e in out["errors"]] == [ErrorCode.SCHEMA_RETRIEVAL_FAILED]
