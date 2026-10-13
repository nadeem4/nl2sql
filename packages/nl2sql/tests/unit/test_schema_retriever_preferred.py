"""Preferred tables: ranked first and marked *preferred* for the planner.

A datasource lists the tables (usually views its data team built) the planner
should reach for first. On both paths the schema retriever puts them first and
marks them; RBAC is unchanged, so a preferred table the role cannot read still
loses its data.
"""
import json
from types import SimpleNamespace

import pytest

from nl2sql.common.settings import settings

from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery
from nl2sql.pipeline.nodes.schema_retriever.node import SchemaRetrieverNode
from nl2sql.pipeline.nodes.schema_retriever.schema import render_schema_for_prompt
from nl2sql.pipeline.state import SubgraphExecutionState
from nl2sql_adapter_sdk.schema import (
    ColumnContract,
    ColumnMetadata,
    ColumnStatistics,
    SchemaContract,
    SchemaMetadata,
    SchemaSnapshot,
    TableContract,
    TableMetadata,
    TableRef,
)

INVOICES = TableRef(schema_name="main", table_name="invoices")
CUSTOMERS = TableRef(schema_name="main", table_name="customers")
REVENUE = TableRef(schema_name="main", table_name="v_customer_revenue")


def _snapshot() -> SchemaSnapshot:
    contract, metadata = {}, {}
    for ref in (INVOICES, CUSTOMERS, REVENUE):
        contract[ref.full_name] = TableContract(
            table=ref,
            columns={"total": ColumnContract(name="total", data_type="NUMERIC")},
            foreign_keys=[],
        )
        stats = ColumnStatistics(null_percentage=0, distinct_count=2, min_value=1, max_value=9,
                                 sample_values=[1, 9])
        metadata[ref.full_name] = TableMetadata(
            table=ref, row_count=2, columns={"total": ColumnMetadata(statistics=stats)}
        )
    return SchemaSnapshot(
        contract=SchemaContract(datasource_id="ds1", engine_type="sqlite", tables=contract),
        metadata=SchemaMetadata(datasource_id="ds1", engine_type="sqlite", tables=metadata),
    )


class _Registry:
    def __init__(self, preferred):
        self._preferred = preferred

    def get_preferred_tables(self, datasource_id):
        return list(self._preferred.get(datasource_id, []))


class _Rbac:
    def __init__(self, allowed):
        self._allowed = allowed

    def get_allowed_tables(self, _user):
        return list(self._allowed)


def _node(preferred, vector_store=None, allowed=("*",)):
    ctx = SimpleNamespace(
        vector_store=vector_store,
        schema_store=SimpleNamespace(get_latest_snapshot=lambda _id: _snapshot()),
        rbac=_Rbac(allowed),
        ds_registry=_Registry({"ds1": preferred}),
    )
    return SchemaRetrieverNode(ctx)


def _state():
    return SubgraphExecutionState(
        trace_id="t", sub_query=SubQuery(id="sq1", datasource_id="ds1", intent="revenue by customer")
    )


def _doc(table, **extra):
    return SimpleNamespace(metadata={"table": table, **extra})


def _vector_store(picked, pool):
    """A store whose table search picks ``picked`` from ``pool`` (table, similarity)."""

    def schema_context(_q, _ds, k=8, explain=None):
        if explain is not None:
            explain.append({
                "search": "tables",
                "pool": [
                    {"type": "schema.table", "table": t, "similarity": s, "picked": t in picked}
                    for t, s in pool
                ],
            })
        return [_doc(t) for t in picked]

    return SimpleNamespace(
        retrieve_schema_context=schema_context,
        retrieve_planning_context=lambda *_a, **_k: [],
        retrieve_column_candidates=lambda *_a, **_k: [],
    )


def test_full_snapshot_lists_preferred_tables_first_and_marks_them():
    result = _node(["v_customer_revenue"])(_state())

    tables = result["relevant_tables"]
    assert [t.name for t in tables][0] == "v_customer_revenue"
    assert tables[0].preferred is True
    assert all(not getattr(t, "preferred", None) for t in tables[1:])


def test_the_planner_schema_block_puts_preferred_tables_first():
    tables = _node(["v_customer_revenue"])(_state())["relevant_tables"]

    rows = [json.loads(line) for line in render_schema_for_prompt(tables).splitlines()]

    assert rows[0]["name"] == "v_customer_revenue"
    assert rows[0]["preferred"] is True
    assert [r["name"] for r in rows[1:]] == ["customers", "invoices"]
    assert "preferred" not in rows[1]


def test_no_preferred_tables_renders_exactly_as_before():
    tables = _node([])(_state())["relevant_tables"]

    block = render_schema_for_prompt(tables)

    assert "preferred" not in block
    assert [json.loads(line)["name"] for line in block.splitlines()] == [
        "customers", "invoices", "v_customer_revenue"
    ]


def test_a_preferred_table_may_be_named_with_its_schema():
    tables = _node(["main.v_customer_revenue"])(_state())["relevant_tables"]

    assert tables[0].name == "v_customer_revenue" and tables[0].preferred is True


def test_an_unreadable_preferred_table_is_still_withheld():
    # The role may read invoices only: the preferred view is ranked and marked
    # like any preferred table, and loses its data like any unreadable one.
    tables = _node(["v_customer_revenue"], allowed=["ds1.invoices"])(_state())["relevant_tables"]

    view = tables[0]
    assert view.name == "v_customer_revenue" and view.preferred is True
    assert all(not c.stats for c in view.columns)
    invoices = next(t for t in tables if t.name == "invoices")
    assert invoices.columns[0].stats


@pytest.fixture
def retrieval(monkeypatch):
    """Force the retrieval path: the three-table schema is over the limit."""
    monkeypatch.setattr(settings, "schema_retrieval_full_snapshot_max_tables", 0)


def test_retrieval_puts_a_found_preferred_table_first(retrieval):
    store = _vector_store(
        picked=[INVOICES.full_name, REVENUE.full_name],
        pool=[(INVOICES.full_name, 0.8), (REVENUE.full_name, 0.7)],
    )

    tables = _node(["v_customer_revenue"], vector_store=store)(_state())["relevant_tables"]

    assert [t.name for t in tables] == ["v_customer_revenue", "invoices"]
    assert tables[0].preferred is True


def test_retrieval_adds_a_missed_preferred_table_within_the_margin(retrieval):
    store = _vector_store(
        picked=[INVOICES.full_name],
        pool=[(INVOICES.full_name, 0.80), (REVENUE.full_name, 0.80 - SchemaRetrieverNode.PREFERRED_MARGIN / 2)],
    )

    tables = _node(["v_customer_revenue"], vector_store=store)(_state())["relevant_tables"]

    assert [t.name for t in tables] == ["v_customer_revenue", "invoices"]


def test_retrieval_leaves_out_a_missed_preferred_table_beyond_the_margin(retrieval):
    store = _vector_store(
        picked=[INVOICES.full_name],
        pool=[(INVOICES.full_name, 0.80), (REVENUE.full_name, 0.80 - SchemaRetrieverNode.PREFERRED_MARGIN * 2)],
    )

    tables = _node(["v_customer_revenue"], vector_store=store)(_state())["relevant_tables"]

    assert [t.name for t in tables] == ["invoices"]


def test_a_context_without_a_registry_still_retrieves():
    ctx = SimpleNamespace(
        vector_store=None,
        schema_store=SimpleNamespace(get_latest_snapshot=lambda _id: _snapshot()),
        rbac=None,
    )

    tables = SchemaRetrieverNode(ctx)(_state())["relevant_tables"]

    assert {t.name for t in tables} == {"customers", "invoices", "v_customer_revenue"}
    assert not any(getattr(t, "preferred", None) for t in tables)
