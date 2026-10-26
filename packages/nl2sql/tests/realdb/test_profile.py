"""The schema profiler on the test bed's PostgreSQL samples (P1).

docs/architecture/wave_b.md, section 6. The profiler reads the live snapshot, so the index is written without
embedding (a snapshot and a pointer); the probes run on the real server through the adapter's
``probe_overlap``.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

from nl2sql import UserContext
from nl2sql_sdk.stores import BuildPointer

ADMIN = UserContext(user_id="ada", roles=[])  # the test bed's engine runs under allow_all


def _profile(engine, *, drop_keys: bool = False):
    """The datasource id, the snapshot stored and the profile of it; ``drop_keys`` stores it without foreign keys."""
    ctx = engine._runtime
    datasource_id = engine.config.datasources[0].id
    adapter = ctx.ds_registry.get_adapter(datasource_id)
    snapshot = adapter.fetch_schema_snapshot(ctx.ds_registry.get_index_scope(datasource_id))
    if drop_keys:  # the database declares its keys; the profiler is for those it does not
        tables = {k: t.model_copy(update={"foreign_keys": []}) for k, t in snapshot.contract.tables.items()}
        snapshot = snapshot.model_copy(update={"contract": snapshot.contract.model_copy(update={"tables": tables})})
    version = ctx.state.put_snapshot(snapshot)
    ctx.index.open(ctx.namespace, 384)
    ctx.index.set_pointers(ctx.namespace, {datasource_id: BuildPointer(
        build_id="b1", schema_version=version, built_at=datetime.now(timezone.utc))})
    [report] = engine.knowledge.profile(user_context=ADMIN)
    return datasource_id, snapshot, report


def test_profile_finds_pagila_joins_with_keys_dropped(server, engine_on):
    if server.name != "postgres":
        pytest.skip("The profiler's expectations are written for pagila on PostgreSQL.")
    engine = engine_on()
    _, snapshot, report = _profile(engine, drop_keys=True)

    proposals = engine.proposals.list(user_context=ADMIN, limit=None)
    joins = {p.target.lower(): p for p in proposals if p.kind == "join"}
    primary = {f"{t.table.table_name}.{c.name}".lower() for t in snapshot.contract.tables.values()
               for c in t.columns.values() if c.is_primary_key}

    assert report.probes > 0
    for suffix in ("rental.inventory_id->inventory.inventory_id", "rental.customer_id->customer.customer_id"):
        [found] = [p for target, p in joins.items() if target.endswith(suffix)]
        assert found.evidence["overlap"] == 1.0 and found.change["cardinality"] == "many-to-one", suffix
    assert all(p.origin == "profiler" for p in proposals)
    for p in proposals:
        if p.kind == "column":  # a key is an identifier, derived: never proposed as a role
            assert p.target.lower() not in primary, p.target
    money = [p for p in proposals if p.kind == "column" and p.target.lower().endswith("payment.amount")]
    assert money and money[0].change["role"] == "money"


def test_wide_schema_profile_stays_under_a_thousand_proposals(server, engine_on):
    if server.name != "postgres":
        pytest.skip("The wide schema is profiled once, on PostgreSQL.")
    if os.environ.get("REALDB_WIDE_ROUTING") != "true":
        pytest.skip("REALDB_WIDE_ROUTING is not true: the wide-schema tests run nightly, on demand, and on pull "
                    "requests that change indexing/.")
    engine = engine_on("wide")
    _, _, report = _profile(engine)

    assert sum(report.proposals.values()) < 1000, report.proposals
    assert report.probes <= 200
