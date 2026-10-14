"""RBAC decides on the schema as well as the table.

A plan's ``TableRef`` carries ``schema_name`` and the generator writes it into
the SQL, so a grant of ``sales.orders`` must not let a plan read
``audit.orders``. Policy entries are ``*``, ``ds.*``, ``ds.schema.*``,
``ds.schema.table`` or the legacy ``ds.table``, which means that table in the
datasource's default schema only. Every node that asks decides with the one
function ``table_allowed``, so the validator, the schema retriever, the
executor's value check and the curated matcher can never disagree.
"""
from __future__ import annotations

import logging
import sqlite3
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from nl2sql.adapters.sqlite.adapter import SqliteAdapter
from nl2sql.auth import RBAC, UserContext
from nl2sql.auth import models as auth_models
from nl2sql.auth.models import RolePolicy
from nl2sql.auth.rbac import default_schema, table_allowed
from nl2sql.pipeline.nodes.ast_planner.schemas import (
    ASTPlannerResponse,
    Expr,
    PlanModel,
    SelectItem,
    TableRef as PlanTableRef,
)
from nl2sql.pipeline.nodes.curated_matcher.node import CuratedMatcherNode, _Fallback
from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery
from nl2sql.pipeline.nodes.schema_retriever.node import SchemaRetrieverNode
from nl2sql.pipeline.nodes.schema_retriever.schema import Column, Table
from nl2sql.pipeline.nodes.validator.node import LogicalValidatorNode
from nl2sql.pipeline.state import GraphState, SubgraphExecutionState
from nl2sql.testing.runtime import fake_runtime
from nl2sql_sdk.schema import TableRef

DS = "shop"
DEFAULT = "sales"


# --- the rule -----------------------------------------------------------------


@pytest.mark.parametrize(
    "entries, schema, table, expected",
    [
        # three-part: exactly that schema
        ([f"{DS}.sales.orders"], "sales", "orders", True),
        ([f"{DS}.sales.orders"], "audit", "orders", False),
        ([f"{DS}.sales.orders"], None, "orders", True),  # unqualified = default schema
        # schema wildcard
        ([f"{DS}.sales.*"], "sales", "anything", True),
        ([f"{DS}.sales.*"], "audit", "anything", False),
        ([f"{DS}.sales.*"], None, "anything", True),
        # legacy two-part: the default schema only
        ([f"{DS}.orders"], None, "orders", True),
        ([f"{DS}.orders"], DEFAULT, "orders", True),
        ([f"{DS}.orders"], "audit", "orders", False),
        # datasource and global wildcards still cover every schema
        ([f"{DS}.*"], "audit", "orders", True),
        (["*"], "audit", "orders", True),
        # another datasource's grant never applies
        (["other.sales.orders"], "sales", "orders", False),
    ],
)
def test_table_allowed_is_schema_aware(entries, schema, table, expected):
    assert table_allowed(entries, DS, table, schema, DEFAULT) is expected


def test_without_a_known_default_main_is_the_default_schema():
    # SQLite and DuckDB keep every table in ``main``.
    assert table_allowed([f"{DS}.orders"], DS, "orders", None, None) is True
    assert table_allowed([f"{DS}.orders"], DS, "orders", "main", None) is True
    assert table_allowed([f"{DS}.orders"], DS, "orders", "audit", None) is False
    assert table_allowed([f"{DS}.main.*"], DS, "orders", None, None) is True
    assert table_allowed([f"{DS}.sales.orders"], DS, "orders", None, None) is False


def test_the_sqlite_adapter_reports_main_as_its_default_schema(tmp_path):
    path = tmp_path / "s.db"
    sqlite3.connect(path).close()
    adapter = SqliteAdapter(datasource_id=DS, datasource_engine_type="sqlite",
                            connection_args={"type": "sqlite", "database": str(path)})

    assert adapter.default_schema == "main"
    assert default_schema(SimpleNamespace(get_adapter=lambda _id: adapter), DS) == "main"


def test_default_schema_is_none_when_the_registry_or_adapter_cannot_say():
    assert default_schema(SimpleNamespace(), DS) is None
    assert default_schema(SimpleNamespace(get_adapter=lambda _id: object()), DS) is None

    def unknown(_id):
        raise ValueError("unknown")

    assert default_schema(SimpleNamespace(get_adapter=unknown), DS) is None


# --- policy validation ----------------------------------------------------------


@pytest.mark.parametrize("entry", ["*", "shop.*", "shop.sales.*", "shop.sales.orders", "shop.orders"])
def test_role_policy_accepts_every_entry_form(entry):
    RolePolicy(description="d", role="r", allowed_tables=[entry])


@pytest.mark.parametrize("entry", ["orders", "shop.*.orders", "shop..orders", "a.b.c.d", "*.orders", "shop.sales."])
def test_role_policy_rejects_a_malformed_entry(entry):
    with pytest.raises(ValidationError):
        RolePolicy(description="d", role="r", allowed_tables=[entry])


def test_a_two_part_entry_warns_once_that_it_means_the_default_schema(monkeypatch, caplog):
    monkeypatch.setattr(auth_models, "_warned_legacy_entry", False)
    with caplog.at_level(logging.WARNING, logger="nl2sql.auth.models"):
        RolePolicy(description="d", role="r", allowed_tables=["shop.orders"])
        RolePolicy(description="d", role="r", allowed_tables=["shop.customers", "shop.sales.*"])
    warnings = [r for r in caplog.records if "default schema" in r.getMessage()]
    assert len(warnings) == 1


def test_three_part_entries_do_not_warn(monkeypatch, caplog):
    monkeypatch.setattr(auth_models, "_warned_legacy_entry", False)
    with caplog.at_level(logging.WARNING, logger="nl2sql.auth.models"):
        RolePolicy(description="d", role="r", allowed_tables=["*", "shop.*", "shop.sales.orders"])
    assert not [r for r in caplog.records if "default schema" in r.getMessage()]


# --- the nodes agree ------------------------------------------------------------


def _rbac(*entries: str) -> RBAC:
    return RBAC({"r": RolePolicy(description="d", role="r", allowed_datasources=[DS], allowed_tables=list(entries))})


def _registry():
    adapter = SimpleNamespace(default_schema=DEFAULT)
    return SimpleNamespace(get_adapter=lambda _id: adapter)


def _validator_allows(rbac: RBAC, schema, table) -> bool:
    plan = PlanModel(
        query_type="READ",
        tables=[PlanTableRef(name=table, schema_name=schema, alias="t0")],
        select_items=[SelectItem(expr=Expr(kind="column", alias="t0", column_name="id"))],
        joins=[],
    )
    state = SubgraphExecutionState(
        trace_id="t",
        sub_query=SubQuery(id="sq1", datasource_id=DS, intent="orders"),
        relevant_tables=[Table(name=table, columns=[Column(name="id", type="INTEGER")])],
        ast_planner_response=ASTPlannerResponse(plan=plan),
        user_context=UserContext(roles=["r"]),
    )
    node = LogicalValidatorNode(fake_runtime(ds_registry=_registry(), rbac=rbac))
    return not node._validate_policy(state)


def _matcher_allows(rbac: RBAC, schema, table) -> bool:
    node = SimpleNamespace(rbac=rbac, ds_registry=_registry())
    state = GraphState(user_query="q", user_context=UserContext(roles=["r"]))
    try:
        CuratedMatcherNode._check_access(node, state, DS, [(schema, table)])
    except _Fallback:
        return False
    return True


def _retriever_allows(rbac: RBAC, schema, table) -> bool:
    node = SchemaRetrieverNode.__new__(SchemaRetrieverNode)
    node.rbac, node.ds_registry = rbac, _registry()
    state = SubgraphExecutionState(trace_id="t", user_context=UserContext(roles=["r"]))
    return node._readable(state, DS)(TableRef(schema_name=schema or DEFAULT, table_name=table))


CASES = [
    ((f"{DS}.sales.orders",), "audit", "orders", False),
    ((f"{DS}.sales.orders",), "sales", "orders", True),
    ((f"{DS}.sales.*",), "sales", "orders", True),
    ((f"{DS}.sales.*",), "audit", "orders", False),
    ((f"{DS}.orders",), None, "orders", True),
    ((f"{DS}.orders",), DEFAULT, "orders", True),
    ((f"{DS}.orders",), "audit", "orders", False),
]


@pytest.mark.parametrize("entries, schema, table, expected", CASES)
def test_the_validator_refuses_a_table_in_a_schema_it_was_not_granted(entries, schema, table, expected):
    assert _validator_allows(_rbac(*entries), schema, table) is expected


@pytest.mark.parametrize("entries, schema, table, expected", CASES)
def test_the_curated_matcher_and_the_validator_agree(entries, schema, table, expected):
    rbac = _rbac(*entries)
    assert _matcher_allows(rbac, schema, table) is _validator_allows(rbac, schema, table) is expected


@pytest.mark.parametrize("entries, schema, table, expected", CASES)
def test_the_schema_retriever_and_the_validator_agree(entries, schema, table, expected):
    rbac = _rbac(*entries)
    assert _retriever_allows(rbac, schema, table) is _validator_allows(rbac, schema, table) is expected


def test_the_executor_value_check_asks_with_the_schema(monkeypatch):
    from nl2sql.pipeline.nodes.executor import node as executor_module

    seen = []

    def fake_check_values(plan, adapter, allowed, can_retry):
        seen.extend(allowed(t) for t in plan.tables)
        return []

    monkeypatch.setattr(executor_module, "check_values", fake_check_values)
    plan = PlanModel(
        query_type="READ",
        tables=[PlanTableRef(name="orders", schema_name="audit", alias="a"),
                PlanTableRef(name="orders", schema_name="sales", alias="s")],
        select_items=[SelectItem(expr=Expr(kind="column", alias="a", column_name="id"))],
        joins=[],
    )
    node = executor_module.ExecutorNode(fake_runtime(rbac=_rbac(f"{DS}.sales.orders"), ds_registry=_registry()))
    state = SubgraphExecutionState(
        trace_id="t",
        sub_query=SubQuery(id="sq1", datasource_id=DS, intent="orders"),
        ast_planner_response=ASTPlannerResponse(plan=plan),
        user_context=UserContext(roles=["r"]),
    )
    response = SimpleNamespace(errors=[], metrics={"row_count": 0})

    node._check_values(state, DS, response)

    assert seen == [False, True]


# --- the shipped policies still load and mean what they meant -------------------


@pytest.mark.parametrize("name", ["policies.demo.json", "policies.example.json"])
def test_the_shipped_policies_still_validate(name):
    import json
    from pathlib import Path

    from nl2sql.auth.models import RolePolicy as Policy

    root = Path(__file__).resolve().parents[4]
    data = json.loads((root / "configs" / name).read_text(encoding="utf-8"))
    for role in data["roles"].values():
        Policy(**role)


def test_the_refusal_names_the_schema_only_outside_the_default_one():
    rbac = _rbac(f"{DS}.sales.customers")

    def refused(schema):
        plan = PlanModel(
            query_type="READ",
            tables=[PlanTableRef(name="orders", schema_name=schema, alias="t0")],
            select_items=[SelectItem(expr=Expr(kind="column", alias="t0", column_name="id"))],
            joins=[],
        )
        state = SubgraphExecutionState(
            trace_id="t",
            sub_query=SubQuery(id="sq1", datasource_id=DS, intent="orders"),
            relevant_tables=[Table(name="orders", columns=[Column(name="id", type="INTEGER")])],
            ast_planner_response=ASTPlannerResponse(plan=plan),
            user_context=UserContext(roles=["r"]),
        )
        node = LogicalValidatorNode(fake_runtime(ds_registry=_registry(), rbac=rbac))
        return node._validate_policy(state)[0].details["table"]

    assert refused(None) == f"{DS}.orders"
    assert refused(DEFAULT) == f"{DS}.orders"
    assert refused("audit") == f"{DS}.audit.orders"
