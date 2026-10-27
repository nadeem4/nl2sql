"""``engine.index.inspect`` shows a caller only the entries their scope reaches (B-K3b; wave_b.md, D60).

It reads the same scope the query path reads and the same visibility
``engine.knowledge.show`` applies: a denied table's entries, a term on one and
any entry whose text names one never reach the caller. A real engine on a small
SQLite database with a fake embedder; nothing is downloaded or called.
"""
from __future__ import annotations

import pytest
from index_helpers import Chunk
from test_knowledge_sources import ADMIN, ANALYST, NOBODY, _engine
from test_public_facade_schema_index import ADMIN as FACADE_ADMIN
from test_public_facade_schema_index import _Context, _snapshot
from test_public_facade_schema_index import _engine as _facade_engine
from test_retrieval_trace import QUESTION, _store


@pytest.fixture
def engine(tmp_path):
    with _engine(tmp_path) as engine:
        assert engine.index.build().ok
        yield engine


def _pool(engine, caller, **kwargs):
    return engine.index.inspect("revenue salaries orders", k=50, user_context=caller, **kwargs)["pool"]


def _mentions_salaries(entry) -> bool:
    return "salaries" in " ".join(str(entry.get(f) or "") for f in ("table", "label", "text")).lower()


def test_a_scoped_caller_sees_no_entry_of_a_denied_table(engine):
    admin, analyst = _pool(engine, ADMIN), _pool(engine, ANALYST)

    assert any(e["type"] == "schema.table" and "salaries" in e["table"] for e in admin)
    assert not any(_mentions_salaries(e) for e in analyst)
    assert any(e["type"] == "schema.table" and "[orders]" in e["table"] for e in analyst)


def test_a_scoped_caller_does_not_see_a_hidden_term(engine):
    terms = lambda pool: {e["label"] for e in pool if e["type"] == "knowledge.term"}  # noqa: E731

    assert terms(_pool(engine, ADMIN)) == {"revenue", "payroll", "cost"}
    # payroll names a denied table; cost's definition mentions one.
    assert terms(_pool(engine, ANALYST)) == {"revenue"}


def test_a_caller_whose_scope_reaches_nothing_sees_nothing(engine):
    assert _pool(engine, NOBODY) == []


def test_recall_holds_for_a_scoped_caller(engine):
    record = engine.index.inspect("orders", k=3, user_context=ANALYST)

    assert len(record["picks"]) == 3


def test_the_caller_is_required(engine):
    with pytest.raises(TypeError):
        engine.index.inspect("orders")
    with pytest.raises(TypeError, match="user_context is required"):
        engine.index.inspect("orders", user_context=None)


def test_a_relationship_needs_both_ends(engine):
    link = lambda pool: [e for e in pool if e["label"] == "[main].[salaries] -> [main].[customers]"]  # noqa: E731

    assert link(_pool(engine, ADMIN))
    assert link(_pool(engine, ANALYST)) == []


def test_verified_query_entries_are_never_shown(tmp_path):
    ctx = _Context(tmp_path, snapshot=_snapshot())
    ctx.test_index.write("chinook", [
        Chunk("Datasource: chinook", type="schema.datasource", datasource_id="chinook"),
        Chunk("top albums", type="curated.verified_query", datasource_id="chinook", query_id="q1")])

    pool = _facade_engine(ctx).index.inspect("top albums", k=5, user_context=FACADE_ADMIN)["pool"]

    assert [e["type"] for e in pool] == ["schema.datasource"]


def test_a_reachable_but_unindexed_datasource_shows_nothing(tmp_path):
    ctx = _Context(tmp_path)  # no snapshot in the state store
    ctx.test_index.write("chinook", [Chunk("Datasource: chinook", type="schema.datasource", datasource_id="chinook")])

    assert _facade_engine(ctx).index.inspect("chinook", k=5, user_context=FACADE_ADMIN)["pool"] == []


def test_the_scoped_pool_fills_from_farther_entries(tmp_path):
    """The over-fetch: when the nearest entries are all hidden, the pool still fills (fails at SCOPE_OVERFETCH = 1)."""
    store = _store(tmp_path)
    nearest = {e["id"] for e in store.inspect(QUESTION, k=2)["pool"][:3]}

    pool = store.inspect(QUESTION, k=1, fetch_k=2, allow=lambda hit: hit.metadata["id"] not in nearest)["pool"]

    assert len(pool) == 2 and not nearest & {e["id"] for e in pool}
