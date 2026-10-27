"""``engine.index.inspect`` shows a caller only the entries their scope reaches (B-K3b; wave_b.md, D60).

It reads the same scope the query path reads and the same visibility
``engine.knowledge.show`` applies: a denied table's entries, a term on one and
any entry whose text names one never reach the caller. A real engine on a small
SQLite database with a fake embedder; nothing is downloaded or called.
"""
from __future__ import annotations

import pytest
from test_knowledge_sources import ADMIN, ANALYST, NOBODY, _engine


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
