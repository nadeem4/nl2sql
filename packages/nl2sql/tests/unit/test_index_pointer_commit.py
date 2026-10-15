"""The build pointer is the commit (bug B5), and one namespace per embedding model (bug B25).

Indexing stores the schema snapshot before it writes the index. Readers used
to read the state store's *latest* snapshot, so a build that failed after the
snapshot was stored left them describing a schema the live index was never
built from. Now each datasource's ``BuildPointer`` records the
``schema_version`` its live build was made from, every reader resolves "live"
through it, and switching the pointer is the commit. See
docs/architecture/phase2.md, section 4.

Each test builds a real engine on a small SQLite database with a fake
embedder, so nothing is downloaded and no model is called.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import List

import pytest
from langchain_core.embeddings import DeterministicFakeEmbedding

from nl2sql import NL2SQL, Embeddings, UserContext
from nl2sql.testing.config import make_config

SHOP = {"id": "shop", "type": "sqlite", "description": "Orders and customers",
        "connection": {"database": "shop.db"}}


class _Embedder(DeterministicFakeEmbedding):
    """A fake embedder with a model name, that can be told to fail."""

    model: str = "fake-a"
    fail: bool = False

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        if self.fail:
            raise RuntimeError("the embedding service went away")
        return super().embed_documents(texts)


def _database(path: Path, *tables: str) -> None:
    with sqlite3.connect(path) as connection:
        for table in tables:
            connection.execute(f"CREATE TABLE IF NOT EXISTS {table} (id INTEGER PRIMARY KEY, name TEXT)")


def _engine(tmp_path: Path, embedder=None, max_versions: int = 3) -> NL2SQL:
    if not (tmp_path / "shop.db").exists():
        _database(tmp_path / "shop.db", "customers")
    cfg = make_config(tmp_path, datasources=[SHOP], llm={"provider": "openai", "model": "gpt-test", "api_key": "sk-test"},
                      settings={"schema": {"max_versions": max_versions}})
    embedder = embedder if embedder is not None else _Embedder(size=8)
    return NL2SQL(cfg.model_copy(update={"embeddings": Embeddings(instance=embedder, dimension=embedder.size)}))


def _tables(engine: NL2SQL) -> List[str]:
    return [t["name"] for t in engine.datasources.schema("shop", user_context=UserContext())["tables"]]


@pytest.fixture
def engine(tmp_path):
    with _engine(tmp_path) as engine:
        assert engine.index.build().ok
        yield engine


def _failed_rebuild(engine: NL2SQL, tmp_path: Path) -> str:
    """Adds a table, then rebuilds with an embedder that fails after the snapshot is stored.

    Returns the schema version the failed build stored.
    """
    _database(tmp_path / "shop.db", "orders")
    ctx = engine._runtime
    stored = []
    put = ctx.state.put_snapshot

    def spy(snapshot):
        version = put(snapshot)
        stored.append(version)
        return version

    ctx.state.put_snapshot = spy
    ctx.embedder.fail = True
    try:
        assert not engine.index.build().ok
    finally:
        ctx.embedder.fail = False
        ctx.state.put_snapshot = put
    assert stored, "the failed build never stored its snapshot"
    return stored[0]


def test_failed_build_leaves_the_live_schema_version(engine, tmp_path):
    ctx = engine._runtime
    live = ctx.vector_store.schema_version("shop")

    stored = _failed_rebuild(engine, tmp_path)

    assert stored != live
    assert ctx.vector_store.schema_version("shop") == live
    assert _tables(engine) == ["customers"]


def test_readers_use_the_pointers_schema_version(engine, tmp_path):
    from nl2sql import UserContext
    from nl2sql.pipeline.nodes.datasource_resolver.node import DatasourceResolverNode
    from nl2sql.pipeline.nodes.schema_retriever.node import SchemaRetrieverNode

    ctx = engine._runtime
    live = ctx.vector_store.schema_version("shop")
    _failed_rebuild(engine, tmp_path)

    resolver = DatasourceResolverNode(ctx)
    assert resolver._direct_candidate("shop").schema_version == live
    assert resolver._summary(UserContext(roles=["admin"]), "shop")[1] == ["customers"]

    snapshot = SchemaRetrieverNode(ctx)._resolve_snapshot("shop", None)
    assert sorted(t.table.table_name for t in snapshot.contract.tables.values()) == ["customers"]

    assert _tables(engine) == ["customers"]


def test_run_pins_pointers_across_a_rebuild(engine, tmp_path):
    ctx = engine._runtime
    run = ctx.pin_index()
    before = run.vector_store.schema_version("shop")

    _database(tmp_path / "shop.db", "orders")
    assert engine.index.build().ok

    assert ctx.vector_store.schema_version("shop") != before
    assert run.vector_store.schema_version("shop") == before
    # The pinned build's entries survive one rebuild, so the run still finds them.
    tables = [d.metadata["table"] for d in run.vector_store.retrieve_schema_context("orders", "shop", k=8)]
    assert len(tables) == 1 and "customers" in tables[0]
    assert sorted(t.table.table_name for t in run.vector_store.snapshot("shop").contract.tables.values()) \
        == ["customers"]


def test_model_change_opens_an_empty_namespace(engine, tmp_path):
    engine.close()
    with _engine(tmp_path, _Embedder(size=16, model="fake-b")) as other:
        health = other.index.health()

        assert health["status"] == "missing"
        assert any("nl2sql index" in p for p in health["problems"])
        assert other.index.build().ok
        assert other.index.health()["status"] == "ok"
        assert len(other._runtime.index.namespaces()) == 2


def test_switching_back_finds_the_old_namespace_built(engine, tmp_path):
    engine.close()
    with _engine(tmp_path, _Embedder(size=16, model="fake-b")) as other:
        assert other.index.build().ok
    with _engine(tmp_path) as again:
        assert again.index.health()["status"] == "ok"
        assert _tables(again) == ["customers"]


def test_prune_keeps_only_the_current_namespace(engine, tmp_path):
    engine.close()
    with _engine(tmp_path, _Embedder(size=16, model="fake-b")) as other:
        assert other.index.build().ok
        index = other._runtime.index
        index.open("nl2sql_store", 16)  # 0.5's collection, which 0.6 never reads

        dropped = other.index.prune()

        current = other._runtime.namespace
        assert index.namespaces() == [current]
        assert sorted(dropped) == sorted(["nl2sql_store", "nl2sql__instance-fake-a__8"])
        assert other.index.health()["status"] == "ok"


def test_the_namespace_names_the_model_and_dimension(engine):
    assert engine._runtime.namespace == "nl2sql__instance-fake-a__8"


def test_keep_snapshots_keeps_every_pointed_version(engine, tmp_path):
    """Eviction never removes a version a pointer names, in any namespace.

    Model A's build points at the first version. Model B then builds three
    more structures with ``max_versions: 1``: its oldest unpointed one goes,
    and model A's pointed one stays, so switching back still reads its schema.
    """
    ctx = engine._runtime
    pointed_by_a = ctx.vector_store.schema_version("shop")
    engine.close()
    versions = []
    with _engine(tmp_path, _Embedder(size=16, model="fake-b"), max_versions=1) as other:
        for table in ("orders", "items", "payments"):
            _database(tmp_path / "shop.db", table)
            assert other.index.build().ok
            versions.append(other._runtime.vector_store.schema_version("shop"))
        state = other._runtime.state
        kept = set(state.list_versions("shop"))

    # A's pointed version, B's live one and the one it replaced (runs may have pinned it).
    assert kept == {pointed_by_a, versions[2], versions[1]}
    with _engine(tmp_path) as again:
        assert again.index.health()["status"] == "ok"
        assert _tables(again) == ["customers"]


def test_index_build_holds_the_lock(engine, tmp_path):
    """``nl2sql index`` and nl2sql-api can share a ``storage.path``: one build at a time."""
    from nl2sql.storage.sqlite import SqliteState

    ctx = engine._runtime
    held = []
    put = ctx.state.put_snapshot

    def spy(snapshot):
        try:
            with ctx.state.lock("index", ttl_s=60):
                held.append(False)
        except Exception:
            held.append(True)
        return put(snapshot)

    ctx.state.put_snapshot = spy
    try:
        assert engine.index.build().ok
    finally:
        ctx.state.put_snapshot = put
    assert held == [True]

    other = SqliteState(ctx.state.path)
    try:
        with other.lock("index", ttl_s=60):
            result = engine.index.build()
    finally:
        other.close()
    assert not result.ok
    assert result.errors and "index" in result.errors[0]["error"]
    assert engine.index.build().ok   # released again
