"""Schema drift, measured: ``engine.index.check``, ``versions`` and ``diff``, the impact, and ``schema.drifted`` (D1).

docs/architecture/wave_a.md, section 10. The owner wants drift captured and
measured before anything acts on it: a check never re-indexes and never
writes, a build stores the diff that introduced its version, and a caller
sees drift only in the tables its scope may read. ``schema.drifted`` is an
operator event: counts only, on the bus, never on a caller's stream.

Each test builds a real engine on a small SQLite database with a fake
embedder, so nothing is downloaded and no model is called.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import List

import pytest
import yaml
from langchain_core.embeddings import DeterministicFakeEmbedding

from nl2sql import NL2SQL, Embeddings, UserContext
from nl2sql.events import STREAM_EVENTS
from nl2sql.policies import patterns_matching
from nl2sql.testing.config import make_config
from nl2sql_sdk.stores import Filter

SHOP = {"id": "shop", "type": "sqlite", "description": "Orders and customers",
        "connection": {"database": "shop.db"}, "preferred_tables": ["main.orders", "salaries"]}

ROLES = {"admin": {"description": "Everything", "allow": ["*"]},
         "analyst": {"description": "All but pay", "allow": ["shop"], "deny": ["shop.main.salaries"]},
         "hr": {"description": "Pay", "allow": ["shop.main.salaries"]},
         "auditor": {"description": "The s tables", "allow": ["shop.main.s*"]}}

QUERIES = [
    {"id": "order_totals", "description": "Totals", "questions": ["What do orders total?"],
     "sql": "SELECT SUM(o.total) AS total FROM orders AS o"},
    {"id": "customer_names", "description": "Names", "questions": ["Who are the customers?"],
     "sql": "SELECT name FROM customers"},
    {"id": "pay", "description": "Pay", "questions": ["What is the payroll?"],
     "sql": "SELECT SUM(amount) FROM salaries"},
]

ADMIN = UserContext(roles=["admin"])
ANALYST = UserContext(roles=["analyst"])
NOBODY = UserContext(roles=["guest"])


def _sql(path: Path, *statements: str) -> None:
    with sqlite3.connect(path) as connection:
        for statement in statements:
            connection.execute(statement)
    connection.close()


def _engine(tmp_path: Path) -> NL2SQL:
    if not (tmp_path / "shop.db").exists():
        _sql(tmp_path / "shop.db",
             "CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT)",
             "CREATE TABLE orders (id INTEGER PRIMARY KEY, customer_id INTEGER, total REAL)",
             "CREATE TABLE salaries (id INTEGER PRIMARY KEY, amount REAL)",
             "CREATE TABLE stock (id INTEGER PRIMARY KEY, sku TEXT)")
    (tmp_path / "curated.yaml").write_text(
        yaml.safe_dump({"datasources": {"shop": {"verified_queries": QUERIES}}}), encoding="utf-8")
    cfg = make_config(tmp_path, datasources=[SHOP], policies={"roles": ROLES}, curated={"file": "curated.yaml"},
                      llm={"provider": "openai", "model": "gpt-test", "api_key": "sk-test"})
    embedder = DeterministicFakeEmbedding(size=8)
    return NL2SQL(cfg.model_copy(update={"embeddings": Embeddings(instance=embedder, dimension=8)}))


@pytest.fixture
def engine(tmp_path):
    with _engine(tmp_path) as engine:
        assert engine.index.build().ok
        yield engine


@pytest.fixture
def drifted(engine):
    """Events seen on the bus: every ``schema.drifted``."""
    seen: List = []
    engine.events.subscribe(seen.append, names=["schema.drifted"])
    return seen


def _db(engine: NL2SQL) -> Path:
    return engine.config.folder / "shop.db"


def _state_of(engine: NL2SQL):
    ctx = engine._runtime
    pointers = ctx.index.pointers(ctx.namespace)
    entries = len(ctx.index.get(ctx.namespace, Filter()))
    return pointers, entries, ctx.state.list_versions("shop")


# -- the check ---------------------------------------------------------------------


def test_an_unchanged_database_is_unchanged(engine, drifted):
    [report] = engine.index.check(user_context=ADMIN)

    assert report.datasource_id == "shop" and report.status == "unchanged"
    assert report.diff is None and report.impact is None and report.error is None
    assert report.live_version == engine._runtime.index.pointers(engine._runtime.namespace)["shop"].schema_version
    assert drifted == []


def test_check_reports_drift_without_reindexing(engine, drifted):
    before = _state_of(engine)
    _sql(_db(engine), "ALTER TABLE orders ADD COLUMN placed_at TEXT", "DROP TABLE customers")

    [report] = engine.index.check(["shop"], user_context=ADMIN)

    assert report.status == "drifted"
    assert report.diff.to_version is None and report.diff.from_version == report.live_version
    assert [(c.table.table_name, c.column_name) for c in report.diff.columns_added] == [("orders", "placed_at")]
    assert [t.table_name for t in report.diff.tables_dropped] == ["customers"]
    assert _state_of(engine) == before
    [event] = drifted
    assert event.data["detected_by"] == "check" and event.data["to_version"] is None
    assert event.data["from_version"] == report.live_version
    assert event.data["counts"]["columns_added"] == 1 and event.data["counts"]["tables_dropped"] == 1


def test_check_never_writes_the_index(engine):
    ctx = engine._runtime
    before = _state_of(engine)
    _sql(_db(engine), "CREATE TABLE refunds (id INTEGER)")

    engine.index.check(user_context=ADMIN)
    engine.index.check(user_context=ADMIN)

    assert _state_of(engine) == before
    assert all(ctx.state.get_schema_diff("shop", v) is None for v in before[2])


def test_a_datasource_never_indexed_is_not_indexed(tmp_path):
    with _engine(tmp_path) as engine:
        [report] = engine.index.check(user_context=ADMIN)

    assert report.status == "not_indexed" and report.live_version is None and report.diff is None


def test_a_check_that_fails_is_reported_redacted(engine, monkeypatch):
    adapter = engine._runtime.ds_registry.get_adapter("shop")

    def broken(scope):
        raise RuntimeError("could not reach postgresql://reader:s3cret-pass@db.internal/shop")

    monkeypatch.setattr(adapter, "fetch_schema_snapshot", broken)
    [report] = engine.index.check(user_context=ADMIN)

    assert report.status == "failed" and report.diff is None
    assert "could not reach" in report.error and "s3cret-pass" not in report.error


def test_the_check_reads_the_catalog_only(engine, monkeypatch):
    adapter = engine._runtime.ds_registry.get_adapter("shop")
    scopes = []
    real = adapter.fetch_schema_snapshot
    monkeypatch.setattr(adapter, "fetch_schema_snapshot", lambda scope: scopes.append(scope) or real(scope))

    engine.index.check(user_context=ADMIN)

    [scope] = scopes
    assert scope.profile == "catalog" and scope.sample_values is False


# -- builds store their diff -----------------------------------------------------------


def test_build_stores_a_diff_for_a_structural_change_only(engine, drifted):
    ctx = engine._runtime
    first = ctx.index.pointers(ctx.namespace)["shop"].schema_version
    _sql(_db(engine), "INSERT INTO orders (customer_id, total) VALUES (1, 9.5)")  # statistics only
    assert engine.index.build().ok
    second = ctx.index.pointers(ctx.namespace)["shop"].schema_version
    assert ctx.state.get_schema_diff("shop", second) is None
    assert drifted == []

    _sql(_db(engine), "ALTER TABLE orders ADD COLUMN placed_at TEXT")
    assert engine.index.build().ok
    third = ctx.index.pointers(ctx.namespace)["shop"].schema_version

    stored = ctx.state.get_schema_diff("shop", third)
    assert stored.from_version == second and stored.to_version == third
    assert [c.column_name for c in stored.columns_added] == ["placed_at"]
    [event] = drifted
    assert event.data == {"datasource_id": "shop", "from_version": second, "to_version": third,
                           "detected_by": "build", "counts": stored.counts()}
    assert engine.index.diff("shop", user_context=ADMIN) == stored
    assert engine.index.diff("shop", first, user_context=ADMIN) is None
    versions = engine.index.versions("shop", user_context=ADMIN)
    assert versions[0].schema_version == third and versions[0].live and versions[0].has_diff
    assert [v.live for v in versions].count(True) == 1
    assert not any(v.has_diff for v in versions[1:])
    assert all(len(v.fingerprint) == 64 for v in versions)


# -- impact --------------------------------------------------------------------------


def test_impact_lists_broken_verified_queries_and_rules(engine):
    _sql(_db(engine), "ALTER TABLE orders DROP COLUMN total", "DROP TABLE salaries")

    [report] = engine.index.check(user_context=ADMIN)

    impact = report.impact
    assert {q.query_id for q in impact.verified_queries} == {"order_totals", "pay"}
    assert all(q.datasource_id == "shop" and q.reasons for q in impact.verified_queries)
    assert {(r.role, r.kind, r.pattern) for r in impact.policy_rules} == {
        ("analyst", "deny", "shop.main.salaries"), ("hr", "allow", "shop.main.salaries")}
    assert impact.preferred_tables == ["salaries"]


def test_patterns_matching_names_each_roles_pattern_that_matches_a_table():
    from nl2sql.config import Role

    roles = {name: Role(**role) for name, role in ROLES.items()}

    matched = patterns_matching(roles, "shop", [("main", "salaries")])

    assert matched == {("admin", "allow", "*"), ("analyst", "allow", "shop"),
                       ("analyst", "deny", "shop.main.salaries"), ("hr", "allow", "shop.main.salaries"),
                       ("auditor", "allow", "shop.main.s*")}
    assert patterns_matching(roles, "other", [("main", "salaries")]) == {("admin", "allow", "*")}


# -- the caller's scope ------------------------------------------------------------------


def test_a_caller_sees_drift_only_in_tables_it_may_read(engine, drifted):
    _sql(_db(engine), "DROP TABLE salaries", "CREATE TABLE secret_bonus (id INTEGER)")
    _sql(_db(engine), "ALTER TABLE orders ADD COLUMN placed_at TEXT")

    [report] = engine.index.check(user_context=UserContext(roles=["analyst"]))

    text = report.model_dump_json()
    assert report.status == "drifted"
    assert "salaries" not in text and "pay" not in text
    assert [c.column_name for c in report.diff.columns_added] == ["placed_at"]
    assert report.impact.policy_rules == [] and report.impact.preferred_tables == []
    # The operator event has the whole diff's counts, and no table names at all.
    [event] = drifted
    assert event.data["counts"]["tables_dropped"] == 1 and "salaries" not in json.dumps(event.data)
    assert "schema.drifted" not in STREAM_EVENTS


def test_drift_only_in_denied_tables_is_unchanged_for_the_caller(engine):
    _sql(_db(engine), "DROP TABLE salaries")
    assert engine.index.build().ok

    [report] = engine.index.check(user_context=ANALYST)

    assert report.status == "unchanged" and report.diff is None
    assert engine.index.diff("shop", user_context=ANALYST) is None
    assert not any(v.has_diff for v in engine.index.versions("shop", user_context=ANALYST))
    assert engine.index.versions("shop", user_context=ADMIN)[0].has_diff


def test_a_datasource_the_caller_cannot_reach_is_left_out(engine):
    _sql(_db(engine), "DROP TABLE customers")

    assert engine.index.check(user_context=NOBODY) == []
    for call in (lambda: engine.index.check(["shop"], user_context=NOBODY),
                 lambda: engine.index.check(["nope"], user_context=ADMIN),
                 lambda: engine.index.versions("shop", user_context=NOBODY),
                 lambda: engine.index.diff("shop", user_context=NOBODY)):
        with pytest.raises(ValueError, match="Unknown datasource ID"):
            call()


def test_user_context_is_required(engine):
    with pytest.raises(TypeError):
        engine.index.check()
    with pytest.raises(TypeError):
        engine.index.diff("shop")
    with pytest.raises(TypeError):
        engine.index.versions("shop")


def test_a_denied_only_change_looks_like_a_statistics_change_to_the_caller(engine):
    """The fingerprint ``versions`` reports is of the contract the caller may see."""
    _sql(_db(engine), "INSERT INTO orders (customer_id, total) VALUES (1, 9.5)")   # statistics only
    assert engine.index.build().ok
    stats_only = engine.index.versions("shop", user_context=ANALYST)
    assert stats_only[0].fingerprint == stats_only[1].fingerprint

    _sql(_db(engine), "ALTER TABLE salaries ADD COLUMN bonus REAL")             # denied to the analyst
    assert engine.index.build().ok

    analyst = engine.index.versions("shop", user_context=ANALYST)
    admin = engine.index.versions("shop", user_context=ADMIN)
    assert analyst[0].fingerprint == analyst[1].fingerprint and not analyst[0].has_diff
    assert admin[0].fingerprint != admin[1].fingerprint and admin[0].has_diff


def test_policy_rule_impact_reads_only_the_tables_the_caller_may_read(engine):
    """``shop.main.s*`` still matches salaries, which the analyst may not read: for it, the rule matches nothing now."""
    _sql(_db(engine), "DROP TABLE stock")

    [admin] = engine.index.check(user_context=ADMIN)
    [analyst] = engine.index.check(user_context=ANALYST)

    rule = ("auditor", "allow", "shop.main.s*")
    assert rule not in {(r.role, r.kind, r.pattern) for r in admin.impact.policy_rules}
    assert rule in {(r.role, r.kind, r.pattern) for r in analyst.impact.policy_rules}


def test_a_check_publishes_a_drift_once_until_it_changes(engine, drifted):
    _sql(_db(engine), "ALTER TABLE orders ADD COLUMN placed_at TEXT")

    engine.index.check(user_context=ADMIN)
    engine.index.check(user_context=ADMIN)
    engine.index.check(user_context=ANALYST)
    assert len(drifted) == 1

    _sql(_db(engine), "DROP TABLE customers")
    engine.index.check(user_context=ADMIN)
    assert len(drifted) == 2
    assert drifted[1].data["counts"]["tables_dropped"] == 1


def test_verified_query_impact_reads_columns_by_their_table():
    """A column qualified by another table is no hit; an unqualified one in a join is a "may read"."""
    from types import SimpleNamespace

    from nl2sql.config.curated import VerifiedQuery
    from nl2sql.schema.impact import drift_impact
    from nl2sql_sdk.schema import ColumnRef, SchemaDiff, TableRef

    def query(qid, sql):
        return VerifiedQuery(id=qid, description=qid, questions=[f"{qid}?"], sql=sql)

    queries = [query("other_table", "SELECT c.note FROM customers c JOIN orders o ON o.customer_id = c.id"),
               query("this_table", "SELECT o.note FROM customers c JOIN orders o ON o.customer_id = c.id"),
               query("ambiguous", "SELECT note FROM customers c JOIN orders o ON o.customer_id = c.id"),
               query("single", "SELECT note FROM orders")]
    ctx = SimpleNamespace(
        curated=SimpleNamespace(datasources={"shop": SimpleNamespace(verified_queries=queries)}),
        ds_registry=SimpleNamespace(get_dialect=lambda ds: "sqlite", get_preferred_tables=lambda ds: []),
        config=SimpleNamespace(policies=SimpleNamespace(roles={})))
    orders = TableRef(schema_name="main", table_name="orders")
    diff = SchemaDiff(datasource_id="shop", from_version="v1",
                      columns_dropped=[ColumnRef(table=orders, column_name="note")])

    impact = drift_impact(ctx, diff, [orders])

    reasons = {q.query_id: q.reasons for q in impact.verified_queries}
    assert set(reasons) == {"this_table", "ambiguous", "single"}
    assert reasons["this_table"] == ["reads dropped column main.orders.note"]
    assert reasons["single"] == ["reads dropped column main.orders.note"]
    assert reasons["ambiguous"] == ["may read dropped column main.orders.note"]


def test_drift_impact_lists_glossary_items():
    """Wave B's K1: glossary items that named a dropped table or column, by one more finder and one more field."""
    from datetime import datetime, timezone
    from types import SimpleNamespace

    from nl2sql.knowledge.merge import LoadedSource, merge
    from nl2sql.schema.impact import DriftImpact, drift_impact
    from nl2sql_sdk.knowledge import Glossary
    from nl2sql_sdk.schema import (ColumnRef, SchemaContract, SchemaDiff, SchemaMetadata, SchemaSnapshot,
                                   TableContract, TableRef)

    glossary = Glossary.model_validate({"version": 1, "datasources": {"shop": {
        "tables": {"salaries": {"description": "Pay."}, "orders": {"description": "Orders."}},
        "columns": {"orders.note": {"description": "Gone."}, "orders.total": {"description": "Kept."},
                    "main.salaries.amount": {"description": "Gone with its table."}},
        "joins": [{"from": "salaries.id", "to": "orders.id"}, {"from": "orders.id", "to": "customers.id"}],
        "terms": [{"term": "payroll", "tables": ["Salaries"]}, {"term": "notes", "columns": ["orders.note"]},
                  {"term": "revenue", "columns": ["orders.total"]},
                  {"term": "audit", "definition": "Joins secrets to orders.", "columns": ["orders.note"]}],
        "rules": [{"id": "pay_private", "text": "Private.", "tables": ["salaries"]}]}}})
    knowledge = merge([LoadedSource(type="yaml", glossary=glossary, sha256=None, path=None,
                                    loaded_at=datetime.now(timezone.utc))])
    orders, salaries = TableRef(schema_name="main", table_name="orders"), TableRef(schema_name="main",
                                                                                     table_name="salaries")
    stored = SchemaSnapshot(
        contract=SchemaContract(datasource_id="shop", engine_type="sqlite", tables={
            name: TableContract(table=TableRef(schema_name="main", table_name=name))
            for name in ("orders", "salaries", "customers", "secrets")}),
        metadata=SchemaMetadata(datasource_id="shop", engine_type="sqlite"))
    ctx = SimpleNamespace(curated=None, knowledge=knowledge, config=SimpleNamespace(policies=SimpleNamespace(roles={})),
                          ds_registry=SimpleNamespace(get_preferred_tables=lambda ds: []),
                          vector_store=SimpleNamespace(snapshot=lambda ds: stored))
    diff = SchemaDiff(datasource_id="shop", from_version="v1", tables_dropped=[salaries],
                      columns_dropped=[ColumnRef(table=orders, column_name="note")])

    impact = drift_impact(ctx, diff, [orders])  # the caller reads orders; customers and secrets are not theirs

    assert impact.glossary_items == ["tables.salaries", "columns.orders.note", "columns.main.salaries.amount",
                                     "joins.salaries.id->orders.id", "terms.payroll", "terms.notes",
                                     "rules.pay_private"]
    assert "terms.audit" not in impact.glossary_items, "its text names a table the caller cannot read"
    assert not impact.is_empty()
    assert DriftImpact(glossary_items=["terms.x"]).is_empty() is False
    other = SchemaDiff(datasource_id="other", from_version="v1", tables_dropped=[salaries])
    assert drift_impact(ctx, other, []).glossary_items == []
