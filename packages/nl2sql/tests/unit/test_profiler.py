"""The schema profiler: join candidates, column roles and ``engine.knowledge.profile`` (P1).

docs/architecture/wave_b.md, section 6, and decisions D38, D41, D44, D45, D46 and D61. The pure rules
(``nl2sql.profiler.joins`` and ``roles``) are tested on hand-built snapshots; the engine tests run a real
SQLite database with a fake embedder, so nothing is downloaded and no model is called.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import pytest
import yaml
from langchain_core.embeddings import DeterministicFakeEmbedding

from nl2sql import NL2SQL, Embeddings, UserContext
from nl2sql.profiler.joins import find_candidates
from nl2sql.profiler.roles import find_roles
from nl2sql.testing.config import make_config
from nl2sql_sdk.knowledge import ColumnKnowledge
from nl2sql_sdk.schema import (
    ColumnContract, ColumnMetadata, ColumnStatistics, ForeignKeyContract, SchemaContract, SchemaMetadata,
    SchemaSnapshot, TableContract, TableMetadata, TableRef,
)
from nl2sql_sdk.stores import BuildPointer


# ---------------------------------------------------------------- hand-built snapshots

def _snapshot(tables: Dict[str, dict]) -> SchemaSnapshot:
    """``{"Track": {"columns": [("TrackId", "INTEGER", True)], "fks": [...], "stats": {"Col": ColumnStatistics}}}``."""
    contract, metadata = {}, {}
    for name, spec in tables.items():
        ref = TableRef(schema_name="main", table_name=name)
        columns = {c[0]: ColumnContract(name=c[0], data_type=c[1], is_primary_key=len(c) > 2 and c[2])
                   for c in spec["columns"]}
        fks = [ForeignKeyContract(constrained_columns=[fk[0]], referred_table=TableRef(schema_name="main", table_name=fk[1]),
                                  referred_columns=[fk[2]]) for fk in spec.get("fks", [])]
        key = f"[main].[{name}]"
        contract[key] = TableContract(table=ref, columns=columns, foreign_keys=fks)
        metadata[key] = TableMetadata(table=ref, columns={
            c: ColumnMetadata(statistics=s) for c, s in spec.get("stats", {}).items()})
    return SchemaSnapshot(contract=SchemaContract(datasource_id="db", engine_type="sqlite", tables=contract),
                          metadata=SchemaMetadata(datasource_id="db", engine_type="sqlite", tables=metadata))


def _stats(distinct: int, *, low=None, high=None, values: Optional[list] = None) -> ColumnStatistics:
    return ColumnStatistics(null_percentage=0.0, distinct_count=distinct, min_value=low, max_value=high,
                            sample_values=values or [])


def _allow_all(schema, table) -> bool:
    return True


def _pairs(candidates) -> List[str]:
    return sorted(f"{c.left.table.table_name}.{c.left_column.name}->{c.right.table.table_name}.{c.right_column.name}"
                  for c in candidates)


def _roles(snapshot, *, said=None, allows=_allow_all, enum_max=50):
    return {f"{f.table.table.table_name}.{f.column.name}": f
            for f in find_roles(snapshot, dialect="sqlite", allows=allows, said=said or {},
                                enum_max_distinct=enum_max)}


def _joins(snapshot, *, allows=_allow_all, known=frozenset()):
    return find_candidates(snapshot, dialect="sqlite", allows=allows, known=known)


# ---------------------------------------------------------------- join candidates: names and types

def test_name_rules_find_customer_id_and_CustomerId():
    snapshot = _snapshot({
        "customers": {"columns": [("id", "INTEGER", True), ("name", "TEXT")]},
        "orders": {"columns": [("order_id", "INTEGER", True), ("customer_id", "INTEGER")]},
        "Customer": {"columns": [("CustomerId", "INTEGER", True)]},
        "Invoice": {"columns": [("InvoiceId", "INTEGER", True), ("CustomerId", "INTEGER"), ("TrackKey", "INTEGER")]},
        "Track": {"columns": [("TrackId", "INTEGER", True)]},
        "Notes": {"columns": [("NoteId", "INTEGER", True), ("id_of_nothing", "INTEGER")]},
    })

    found = _pairs(_joins(snapshot))

    assert "orders.customer_id->customers.id" in found  # table name (plural) + _id -> the only column named id
    assert "Invoice.CustomerId->Customer.CustomerId" in found  # the column's name equals the key's
    assert "Invoice.TrackKey->Track.TrackId" in found  # table name + key
    assert not any(pair.startswith("Notes.") for pair in found)
    assert not any(pair.startswith("customers.id") or pair.startswith("orders.order_id") for pair in found)


def test_a_bare_id_key_is_not_matched_by_every_id():
    snapshot = _snapshot({"a": {"columns": [("id", "INTEGER", True)]}, "b": {"columns": [("id", "INTEGER", True)]}})
    assert _joins(snapshot) == []


def test_type_category_mismatch_drops_a_candidate():
    snapshot = _snapshot({
        "customers": {"columns": [("id", "INTEGER", True)]},
        "orders": {"columns": [("customer_id", "TEXT")]},
        "visits": {"columns": [("customer_id", "BLOB")]},  # `other` never matches
    })
    assert _joins(snapshot) == []


def test_declared_foreign_key_is_not_proposed():
    snapshot = _snapshot({
        "customers": {"columns": [("id", "INTEGER", True)]},
        "orders": {"columns": [("customer_id", "INTEGER"), ("buyer_id", "INTEGER")],
                   "fks": [("customer_id", "customers", "id")]},
    })
    # customer_id is a foreign key; and with a key between orders and customers, no other column pair is proposed.
    assert _joins(snapshot) == []


def test_glossary_join_is_not_proposed_again():
    snapshot = _snapshot({
        "customers": {"columns": [("id", "INTEGER", True)]},
        "orders": {"columns": [("customer_id", "INTEGER")]},
    })
    known = frozenset({frozenset({("[main].[orders]", "customer_id"), ("[main].[customers]", "id")})})
    assert _joins(snapshot, known=known) == []
    assert _pairs(_joins(snapshot)) == ["orders.customer_id->customers.id"]


def test_a_denied_table_is_not_a_candidate():
    snapshot = _snapshot({
        "customers": {"columns": [("id", "INTEGER", True)]},
        "orders": {"columns": [("customer_id", "INTEGER")]},
    })
    assert _joins(snapshot, allows=lambda schema, table: table != "customers") == []


def test_cardinality_and_strength():
    snapshot = _snapshot({
        "Customer": {"columns": [("CustomerId", "INTEGER", True)]},
        "Invoice": {"columns": [("CustomerId", "INTEGER")]},
        "Pet": {"columns": [("pet_code", "INTEGER")]},
        "Owner": {"columns": [("id", "INTEGER")]},  # not a primary key: the only column named id
        "Visit": {"columns": [("owner_id", "INTEGER")]},
    })
    by_pair = {p: c for c in _joins(snapshot) for p in _pairs([c])}

    exact = by_pair["Invoice.CustomerId->Customer.CustomerId"]
    assert exact.cardinality == "many-to-one" and exact.rule == "same_name"
    assert by_pair["Visit.owner_id->Owner.id"].cardinality == "unknown"
    assert _joins(snapshot)[0].rule == "same_name"  # the exact key-name match comes first


def test_a_shared_primary_key_name_is_one_one_to_one_pair():
    snapshot = _snapshot({"film": {"columns": [("film_id", "INTEGER", True)]},
                          "film_text": {"columns": [("film_id", "INTEGER", True)]}})

    [candidate] = _joins(snapshot)

    assert _pairs([candidate]) == ["film.film_id->film_text.film_id"]
    assert candidate.cardinality == "one-to-one"


def test_candidates_on_a_wide_schema_take_a_moment_not_a_minute():
    import time

    tables = {}
    for i in range(1200):
        name = f"noun{i:04d}s"
        tables[name] = {"columns": [(f"noun{i:04d}_id", "INTEGER", True)]
                        + [(f"col_{j}", "TEXT") for j in range(14)]
                        + [(f"noun{(i + 1) % 1200:04d}_id", "INTEGER")]}
    snapshot = _snapshot(tables)

    started = time.monotonic()
    found = _joins(snapshot)

    assert time.monotonic() - started < 3
    assert len(found) == 1200


# ---------------------------------------------------------------- column roles

def test_derived_roles_are_not_proposed():
    snapshot = _snapshot({"orders": {
        "columns": [("order_id", "INTEGER", True), ("customer_id", "INTEGER"), ("placed", "DATETIME"),
                    ("total", "NUMERIC")],
        "fks": [("customer_id", "customers", "id")],
        "stats": {"order_id": _stats(2), "customer_id": _stats(2), "placed": _stats(2)}}})

    found = _roles(snapshot)

    assert set(found) == {"orders.total"}  # keys are identifiers and a temporal type a date: derived, not proposed


@pytest.mark.parametrize("name, data_type, unit", [
    ("Total", "NUMERIC", None), ("unit_price", "DECIMAL(10,2)", None), ("amount_usd", "REAL", "USD"),
    ("salary_eur", "NUMERIC", "EUR"), ("RevenueGbp", "FLOAT", "GBP"),
])
def test_money_role(name, data_type, unit):
    snapshot = _snapshot({"t": {"columns": [(name, data_type)]}})
    change = _roles(snapshot)[f"t.{name}"].change
    assert change == ({"role": "money", "unit": unit} if unit else {"role": "money"})


def test_money_needs_a_decimal_column():
    assert _roles(_snapshot({"t": {"columns": [("total", "TEXT"), ("price", "INTEGER")]}})).get("t.total") is None
    assert "t.price" not in _roles(_snapshot({"t": {"columns": [("price", "INTEGER")]}})) \
        or _roles(_snapshot({"t": {"columns": [("price", "INTEGER")]}}))["t.price"].change["role"] != "money"


@pytest.mark.parametrize("name, data_type, unit", [
    ("Quantity", "INTEGER", None), ("qty", "NUMERIC", None), ("units_sold", "INTEGER", None),
    ("order_count", "INTEGER", None), ("weight_kg", "REAL", "kg"), ("latency_ms", "INTEGER", "ms"),
    ("discount_pct", "NUMERIC", "pct"),
])
def test_quantity_role(name, data_type, unit):
    change = _roles(_snapshot({"t": {"columns": [(name, data_type)]}}))[f"t.{name}"].change
    assert change == ({"role": "quantity", "unit": unit} if unit else {"role": "quantity"})


def test_enum_role_on_text_and_integer_columns():
    snapshot = _snapshot({"t": {
        "columns": [("t_id", "INTEGER", True), ("status", "TEXT"), ("level", "INTEGER"), ("notes", "TEXT"),
                    ("name", "TEXT"), ("owner_id", "INTEGER")],
        "stats": {"status": _stats(3), "level": _stats(4), "notes": _stats(51), "name": _stats(2),
                  "owner_id": _stats(3)}}})

    found = _roles(snapshot)

    assert found["t.status"].change == {"role": "enum"} and found["t.level"].change == {"role": "enum"}
    assert "t.notes" not in found  # over enum_max_distinct
    assert "t.t_id" not in found and "t.owner_id" not in found  # an identifier, and a key by its name
    assert "t.status" in _roles(snapshot, enum_max=3) and "t.level" not in _roles(snapshot, enum_max=3)
    assert found["t.status"].evidence["distinct"] == 3


def test_enum_values_only_with_sample_values():
    with_values = _snapshot({"t": {"columns": [("status", "TEXT")],
                                   "stats": {"status": _stats(2, values=["new", "paid"])}}})
    without = _snapshot({"t": {"columns": [("status", "TEXT")], "stats": {"status": _stats(2)}}})
    partial = _snapshot({"t": {"columns": [("status", "TEXT")],
                               "stats": {"status": _stats(7, values=["a", "b", "c", "d", "e"])}}})

    assert _roles(with_values)["t.status"].change == {"role": "enum", "values": {"new": "", "paid": ""}}
    assert _roles(without)["t.status"].change == {"role": "enum"}
    assert _roles(partial)["t.status"].change == {"role": "enum"}  # five of seven is not "all of them"


def test_date_role_on_text_that_holds_iso_dates():
    snapshot = _snapshot({"t": {"columns": [("joined", "TEXT"), ("label", "TEXT"), ("mixed", "TEXT")],
                                "stats": {"joined": _stats(900, low="2020-01-05", high="2024-03-09"),
                                          "label": _stats(900, low="alpha", high="zulu"),
                                          "mixed": _stats(900, low="2020-01-05", high="n/a")}}})

    found = _roles(snapshot)

    assert found["t.joined"].change == {"role": "date"}
    assert "t.label" not in found and "t.mixed" not in found


def test_refined_grain_when_every_value_is_the_first_of_a_month_or_year():
    snapshot = _snapshot({"t": {
        "columns": [("period", "DATE"), ("fiscal", "TEXT"), ("day", "DATE"), ("stamp", "DATETIME")],
        "stats": {
            "period": _stats(3, low="2020-01-01", high="2020-03-01", values=["2020-01-01", "2020-02-01", "2020-03-01"]),
            "fiscal": _stats(2, low="2020-01-01", high="2021-01-01", values=["2020-01-01", "2021-01-01"]),
            "day": _stats(2, low="2020-01-01", high="2020-01-15", values=["2020-01-01", "2020-01-15"]),
            "stamp": _stats(2, low="2020-01-01 00:00:00", high="2020-02-01 00:00:00",
                            values=["2020-01-01 00:00:00", "2020-02-01 00:00:00"]),
        }}})

    found = _roles(snapshot)

    assert found["t.period"].change == {"grain": "month"}  # a date column: the role is derived, only the grain is new
    assert found["t.fiscal"].change == {"role": "date", "grain": "year"}
    assert "t.day" not in found
    assert found["t.stamp"].change == {"grain": "month"}


def test_what_the_glossary_says_is_not_proposed_again():
    snapshot = _snapshot({"t": {"columns": [("total", "NUMERIC"), ("status", "TEXT")],
                                "stats": {"status": _stats(2)}}})
    said = {("[main].[t]", "total"): ColumnKnowledge(role="money"),
            ("[main].[t]", "status"): ColumnKnowledge(description="State of the order.")}

    found = _roles(snapshot, said=said)

    assert "t.total" not in found
    assert found["t.status"].change == {"role": "enum"}  # a description is not a role


# ---------------------------------------------------------------- the engine

ROLES = {"admin": {"description": "Everything", "allow": ["*"]},
         "reader": {"description": "No tickets", "allow": ["shop"], "deny": ["shop.main.tickets"]}}
ADMIN = UserContext(user_id="ada", roles=["admin"])
READER = UserContext(user_id="rae", roles=["reader"])


def _database(folder: Path) -> None:
    if (folder / "shop.db").exists():
        return
    with sqlite3.connect(folder / "shop.db") as connection:
        connection.executescript("""
            CREATE TABLE customers (customer_id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE orders (order_id INTEGER PRIMARY KEY, customer_id INTEGER, total NUMERIC, status TEXT);
            CREATE TABLE tickets (ticket_id INTEGER PRIMARY KEY, customer_id INTEGER);
            CREATE TABLE visits (visit_id INTEGER PRIMARY KEY, customer_id INTEGER);
            INSERT INTO customers VALUES (1, 'a'), (2, 'b'), (3, 'c');
            INSERT INTO orders VALUES (1, 1, 5.0, 'new'), (2, 2, 7.5, 'paid'), (3, 3, 9.0, 'new');
            INSERT INTO tickets VALUES (1, 100), (2, 200), (3, 300);
            INSERT INTO visits VALUES (1, 1), (2, 2), (3, 3);
        """)
    connection.close()


def _engine(folder: Path, *, index: bool = True, settings: Optional[dict] = None, **sections) -> NL2SQL:
    _database(folder)
    config = make_config(
        folder, datasources=[{"id": "shop", "type": "sqlite", "connection": {"database": "shop.db"}}],
        policies={"roles": ROLES}, **({"settings": settings} if settings else {}), **sections)
    engine = NL2SQL(config.model_copy(update={"embeddings": Embeddings(instance=DeterministicFakeEmbedding(size=8),
                                                                       dimension=8)}))
    if index:
        ctx = engine._runtime
        adapter = ctx.ds_registry.get_adapter("shop")
        version = ctx.state.put_snapshot(adapter.fetch_schema_snapshot(ctx.ds_registry.get_index_scope("shop")))
        ctx.index.open(ctx.namespace, 8)
        ctx.index.set_pointers(ctx.namespace, {"shop": BuildPointer(
            build_id="b1", schema_version=version, built_at=datetime.now(timezone.utc))})
    return engine


@pytest.fixture
def engine(tmp_path):
    with _engine(tmp_path) as engine:
        yield engine


def _targets(engine, kind="join") -> Dict[str, object]:
    return {p.target: p for p in engine.proposals.list(user_context=ADMIN, kind=kind)}


def test_profile_files_joins_with_evidence_and_roles(engine):
    [report] = engine.knowledge.profile(user_context=ADMIN)

    assert report.datasource_id == "shop" and report.stopped_by == "done" and report.issues == []
    joins = _targets(engine)
    assert "orders.customer_id->customers.customer_id" in joins
    join = joins["orders.customer_id->customers.customer_id"]
    assert join.origin == "profiler" and join.status == "proposed" and join.created_by is None
    assert join.change == {"from": "orders.customer_id", "to": "customers.customer_id", "cardinality": "many-to-one"}
    assert join.evidence["overlap"] == 1.0 and join.evidence["sampled"] == 3 and join.evidence["matched"] == 3
    assert join.evidence["rule"] == "same_name" and join.evidence["category"] == "number"
    assert join.schema_version == report.schema_version and report.schema_version
    assert sorted(join.tables) == ["shop.main.customers", "shop.main.orders"]
    columns = _targets(engine, "column")
    assert columns["orders.total"].change == {"role": "money"}
    assert columns["orders.status"].change == {"role": "enum"}
    assert report.proposals["join"] >= 2 and report.proposals["column"] == len(columns)
    assert report.probes == report.proposals["join"] + 1  # tickets' 100, 200, 300 are probed and then dropped


def test_low_overlap_drops_a_candidate(engine):
    engine.knowledge.profile(user_context=ADMIN)
    assert "tickets.customer_id->customers.customer_id" not in _targets(engine)


def test_profiling_twice_updates_the_same_rows(engine):
    engine.knowledge.profile(user_context=ADMIN)
    first = {p.id for p in engine.proposals.list(user_context=ADMIN)}
    engine.knowledge.profile(user_context=ADMIN)
    assert {p.id for p in engine.proposals.list(user_context=ADMIN)} == first


def test_budget_proposes_the_rest_without_overlap(tmp_path):
    with _engine(tmp_path, settings={"profiler": {"max_probes": 1}}) as engine:
        [report] = engine.knowledge.profile(user_context=ADMIN)

        joins = _targets(engine)
        assert report.probes == 1 and report.stopped_by == "max_probes"
        assert report.skipped_by_budget == 2
        unprobed = [p for p in joins.values() if p.evidence["overlap"] is None]
        assert len(unprobed) == 2 and all("sampled" not in p.evidence for p in unprobed)


def test_a_probe_that_drops_a_candidate_withdraws_its_earlier_unprobed_proposal(tmp_path):
    folder = tmp_path
    with _engine(folder, settings={"profiler": {"max_probes": 1}}) as first:
        first.knowledge.profile(user_context=ADMIN)
        assert "tickets.customer_id->customers.customer_id" in _targets(first)
    with _engine(folder) as engine:  # same state store, default bounds: the probe now measures it
        engine.knowledge.profile(user_context=ADMIN)

        assert "tickets.customer_id->customers.customer_id" not in _targets(engine)
        withdrawn = [p for p in engine.proposals.list(user_context=ADMIN, status="superseded")]
        assert [p.target for p in withdrawn] == ["tickets.customer_id->customers.customer_id"]


def test_the_time_budget_stops_probing(tmp_path, monkeypatch):
    with _engine(tmp_path, settings={"profiler": {"time_budget_s": 0.5}}) as engine:
        ticks = iter([0.0, 0.1, 10.0, 11.0, 12.0, 13.0, 14.0, 15.0])
        monkeypatch.setattr("nl2sql.profiler.run._now", lambda: next(ticks, 99.0))

        [report] = engine.knowledge.profile(user_context=ADMIN)

        assert report.stopped_by == "time_budget" and report.skipped_by_budget >= 1


def test_denied_table_is_never_probed(tmp_path):
    with _engine(tmp_path) as engine:
        adapter = engine._runtime.ds_registry.get_adapter("shop")
        probed = []
        original = adapter.probe_overlap

        def spy(left, right, sample_rows):
            probed.append((left.table.table_name, right.table.table_name))
            return original(left, right, sample_rows)

        adapter.probe_overlap = spy
        engine.knowledge.profile(user_context=READER)

        assert probed and all("tickets" not in pair for pair in probed)
        for proposal in engine.proposals.list(user_context=ADMIN):
            assert not any(t.endswith(".tickets") for t in proposal.tables)


def test_a_failed_probe_is_an_issue_and_the_candidate_has_no_overlap(tmp_path):
    with _engine(tmp_path) as engine:
        adapter = engine._runtime.ds_registry.get_adapter("shop")

        def broken(left, right, sample_rows):
            raise RuntimeError("probing orders.customer_id against customers.customer_id failed (OperationalError)")

        adapter.probe_overlap = broken
        [report] = engine.knowledge.profile(user_context=ADMIN)

        assert report.issues and "OperationalError" in report.issues[0]
        assert _targets(engine)["orders.customer_id->customers.customer_id"].evidence["overlap"] is None


def test_an_adapter_without_probe_overlap_proposes_by_name_and_type(tmp_path, monkeypatch):
    with _engine(tmp_path) as engine:
        registry = engine._runtime.ds_registry
        real = registry.get_adapter("shop")

        class WithoutProbe:  # an adapter that does not declare the optional method
            def __getattr__(self, name):
                if name == "probe_overlap":
                    raise AttributeError(name)
                return getattr(real, name)

        monkeypatch.setattr(registry, "get_adapter", lambda datasource_id: WithoutProbe())
        [report] = engine.knowledge.profile(user_context=ADMIN)
        assert report.probes == 0 and report.skipped_by_budget == 0
        assert _targets(engine)["tickets.customer_id->customers.customer_id"].evidence["overlap"] is None


def test_profile_requires_a_caller(engine):
    with pytest.raises(TypeError, match="user_context"):
        engine.knowledge.profile(user_context=None)
    with pytest.raises(TypeError):
        engine.knowledge.profile()


def test_profile_skips_an_unindexed_datasource(tmp_path):
    with _engine(tmp_path, index=False) as engine:
        [report] = engine.knowledge.profile(user_context=ADMIN)

        assert report.proposals == {} and report.probes == 0
        assert len(report.issues) == 1 and "nl2sql index" in report.issues[0]
        assert engine.proposals.list(user_context=ADMIN) == []


def test_profile_shows_nothing_for_a_datasource_the_caller_cannot_reach(tmp_path):
    roles = {"admin": ROLES["admin"], "other": {"description": "Elsewhere", "allow": ["nowhere"]}}
    folder = tmp_path
    _database(folder)
    config = make_config(folder, datasources=[{"id": "shop", "type": "sqlite", "connection": {"database": "shop.db"}}],
                         policies={"roles": roles})
    with NL2SQL(config.model_copy(update={"embeddings": Embeddings(instance=DeterministicFakeEmbedding(size=8),
                                                                  dimension=8)})) as engine:
        assert engine.knowledge.profile(user_context=UserContext(roles=["other"])) == []


def test_unknown_datasource_is_a_value_error(engine):
    with pytest.raises(ValueError, match="Unknown datasource"):
        engine.knowledge.profile(["nope"], user_context=ADMIN)


def test_glossary_join_is_not_proposed_again_by_the_engine(tmp_path):
    (tmp_path / "glossary.yaml").write_text(yaml.safe_dump({"version": 1, "datasources": {"shop": {
        "joins": [{"from": "orders.customer_id", "to": "customers.customer_id"}],
        "columns": {"orders.total": {"role": "money"}}}}}), encoding="utf-8")
    with _engine(tmp_path, knowledge=[{"type": "yaml", "path": "glossary.yaml"}]) as engine:
        engine.knowledge.profile(user_context=ADMIN)

        assert "orders.customer_id->customers.customer_id" not in _targets(engine)
        assert "orders.total" not in _targets(engine, "column")


def test_profile_finished_is_published(engine):
    seen = []
    engine._runtime.bus.subscribe(seen.append, names=["profile.finished"])

    [report] = engine.knowledge.profile(user_context=ADMIN)

    [event] = seen
    assert event.data["datasource_id"] == "shop" and event.data["probes"] == report.probes
    assert event.data["proposals"] == report.proposals and event.data["stopped_by"] == "done"
    assert "customers" not in str(event.data)  # counts only, never names or values
