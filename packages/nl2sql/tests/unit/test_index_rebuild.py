"""Re-indexing never leaves an empty or half-built index, and never touches
another datasource's entries.

The old ``nl2sql index`` deleted the whole collection first and rebuilt it
after, so any failure in between left 0 entries and every question failed at
the resolver. Now all datasources share one collection and each is rebuilt on
its own: its new entries are written under a new build id, the datasource's
active build is switched in the collection's metadata only when every entry
is written, and only then are its previous entries deleted. Readers filter on
the active build, so they never see a half-built or mixed set.

A change of embedding model is the one case that needs every datasource
rebuilt at once; that is ``full=True``, which builds a second collection and
swaps it in.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import List

import pytest
from langchain_core.embeddings import Embeddings, FakeEmbeddings

from nl2sql.indexing import rebuild as rebuild_module
from nl2sql.indexing.rebuild import rebuild_index
from nl2sql.indexing.vector_store import EmbeddingModelMismatchError, VectorStore


class _Chunk:
    """The two methods VectorStore needs from a chunk."""

    def __init__(self, ds, kind, text, version):
        self.type = kind
        self._text = text
        self._md = {"type": kind, "datasource_id": ds, "schema_version": version}
        if kind != "schema.datasource":
            self._md["table"] = text

    def get_page_content(self):
        return self._text

    def get_metadata(self):
        return dict(self._md)


def _chunks(ds, version="v1", tables=("T1", "T2", "T3")) -> List[_Chunk]:
    return [_Chunk(ds, "schema.datasource", f"Datasource: {ds}", version)] + [
        _Chunk(ds, "schema.table", f"{ds}.{t}", version) for t in tables
    ]


class _ModelA(FakeEmbeddings):
    model: str = "model-a"


class _ModelB(FakeEmbeddings):
    model: str = "model-b"


def _store(tmp_path, embeddings=None, name="nl2sql_store"):
    return VectorStore(name, str(tmp_path / "vs"), embeddings=embeddings or _ModelA(size=8))


def _entries(store, ds):
    got = store.vectorstore._collection.get(where={"datasource_id": ds})
    return sorted(zip(got["ids"], got["documents"], [tuple(sorted(m.items())) for m in got["metadatas"]]))


def _index(store, ds, version="v1", **kwargs):
    return store.refresh_schema_chunks(ds, version, _chunks(ds, version, **kwargs), [])


# --- per-datasource rebuild ------------------------------------------------------


def test_reindexing_one_datasource_leaves_the_other_byte_identical(tmp_path):
    store = _store(tmp_path)
    _index(store, "ds_a")
    _index(store, "ds_b")
    before_b = _entries(store, "ds_b")

    _index(store, "ds_a", version="v2")

    assert _entries(store, "ds_b") == before_b
    texts_a = sorted(e[1] for e in _entries(store, "ds_a"))
    assert texts_a == ["Datasource: ds_a", "ds_a.T1", "ds_a.T2", "ds_a.T3"]
    assert all(dict(e[2])["schema_version"] == "v2" for e in _entries(store, "ds_a"))


class _FailsAfter(Embeddings):
    """Embeds the first ``ok_calls`` batches, then fails: a build dying midway."""

    model = "model-a"

    def __init__(self, ok_calls):
        self.ok_calls = ok_calls
        self.inner = _ModelA(size=8)

    def embed_documents(self, texts):
        if self.ok_calls <= 0:
            raise RuntimeError("embedding service went away")
        self.ok_calls -= 1
        return self.inner.embed_documents(texts)

    def embed_query(self, text):
        return self.inner.embed_query(text)


def test_a_reindex_that_fails_midway_leaves_the_previous_entries_and_the_other_datasource(tmp_path):
    embeddings = _FailsAfter(ok_calls=100)
    store = _store(tmp_path, embeddings=embeddings)
    _index(store, "ds_a")
    _index(store, "ds_b")
    before_a, before_b = _entries(store, "ds_a"), _entries(store, "ds_b")

    embeddings.ok_calls = 1
    with pytest.raises(RuntimeError, match="went away"):
        store.refresh_schema_chunks("ds_a", "v2", _chunks("ds_a", "v2", tables=("T1", "T2", "T3", "T4")), [],
                                    batch_size=2)

    # The first batch was written and then removed; nothing of v2 is left.
    assert _entries(store, "ds_a") == before_a
    assert _entries(store, "ds_b") == before_b
    docs = store.retrieve_datasource_candidates("anything", k=5)
    assert sorted(d.metadata["datasource_id"] for d in docs) == ["ds_a", "ds_b"]


def test_readers_never_see_both_builds_while_they_coexist(tmp_path):
    store = _store(tmp_path)
    _index(store, "ds_a", version="v1")
    seen = {}

    class _Peek:
        """Held around the switch: the new build is written, not yet active."""

        def __enter__(self):
            all_a = store.vectorstore._collection.get(where={"datasource_id": "ds_a"})["ids"]
            seen["stored"] = len(all_a)
            seen["resolver"] = store.retrieve_datasource_candidates("q", k=5)
            seen["tables"] = store.retrieve_schema_context("q", "ds_a", k=8)

        def __exit__(self, *exc):
            return False

    store.refresh_schema_chunks("ds_a", "v2", _chunks("ds_a", "v2"), [], switch_guard=_Peek)

    assert seen["stored"] == 8  # old and new are both on disk at this moment
    assert [d.metadata["schema_version"] for d in seen["resolver"]] == ["v1"]
    assert {d.metadata["schema_version"] for d in seen["tables"]} == {"v1"}
    assert len(seen["tables"]) == 3
    # After the switch only v2 is visible, and v1 is gone.
    assert [d.metadata["schema_version"] for d in store.retrieve_datasource_candidates("q", k=5)] == ["v2"]
    assert len(_entries(store, "ds_a")) == 4


def test_entries_from_before_build_ids_are_replaced_on_the_first_reindex(tmp_path):
    """An index written by an older engine has no build ids."""
    from langchain_core.documents import Document

    store = _store(tmp_path)
    store.vectorstore.add_documents([Document(page_content="legacy", metadata={
        "type": "schema.datasource", "datasource_id": "ds_a", "schema_version": "v0"})])
    store.vectorstore.add_documents([Document(page_content="legacy b", metadata={
        "type": "schema.datasource", "datasource_id": "ds_b", "schema_version": "v0"})])

    assert sorted(d.page_content for d in store.retrieve_datasource_candidates("q", k=5)) == ["legacy", "legacy b"]
    _index(store, "ds_a", version="v1")

    got = sorted(d.page_content for d in store.retrieve_datasource_candidates("q", k=5))
    assert got == ["Datasource: ds_a", "legacy b"]


# --- one embedding model per collection -----------------------------------------


def test_the_collection_records_its_embedding_model(tmp_path):
    store = _store(tmp_path)
    _index(store, "ds_a")

    assert store.recorded_embedding_model() == "_ModelA/model-a"


def test_a_same_dimension_different_model_is_refused_on_read_and_on_reindex(tmp_path):
    _index(_store(tmp_path), "ds_a")
    other = _store(tmp_path, embeddings=_ModelB(size=8))

    with pytest.raises(EmbeddingModelMismatchError, match="full rebuild"):
        other.retrieve_datasource_candidates("q", k=5)
    with pytest.raises(EmbeddingModelMismatchError, match="--full"):
        _index(other, "ds_a", version="v2")


# --- rebuild_index: the entry point for the CLI, the demo and the playground ------


class _Adapter:
    def __init__(self, datasource_id):
        self.datasource_id = datasource_id


def _ctx(store, *datasource_ids):
    return SimpleNamespace(
        vector_store=store,
        ds_registry=SimpleNamespace(list_adapters=lambda: [_Adapter(d) for d in datasource_ids]),
    )


@pytest.fixture
def scripted(monkeypatch):
    """An orchestrator that writes a small chunk set per datasource, or raises."""
    outcomes: dict = {}

    class _Orchestrator:
        seen = []

        def __init__(self, ctx, enrich=True):
            _Orchestrator.seen.append(("enrich", enrich))

        def index_datasource(self, adapter, vector_store=None, switch_guard=None):
            ds = adapter.datasource_id
            outcome = outcomes.get(ds, "v1")
            if isinstance(outcome, Exception):
                raise outcome
            target = vector_store if vector_store is not None else self_store[0]
            return target.refresh_schema_chunks(ds, outcome, _chunks(ds, outcome), [], switch_guard=switch_guard)

    self_store = []
    monkeypatch.setattr(rebuild_module, "IndexingOrchestrator", _Orchestrator)
    return outcomes, self_store, _Orchestrator


def test_rebuilding_one_datasource_does_not_clear_the_others(tmp_path, scripted):
    outcomes, holder, _ = scripted
    store = _store(tmp_path)
    holder.append(store)
    rebuild_index(_ctx(store, "ds_a", "ds_b"))
    before_b = _entries(store, "ds_b")
    outcomes["ds_a"] = "v2"

    result = rebuild_index(_ctx(store, "ds_a", "ds_b"), datasource_ids=["ds_a"])

    assert result.ok
    assert _entries(store, "ds_b") == before_b
    assert {dict(e[2])["schema_version"] for e in _entries(store, "ds_a")} == {"v2"}


def test_one_failing_datasource_keeps_its_previous_entries_and_the_others_still_rebuild(tmp_path, scripted):
    outcomes, holder, _ = scripted
    store = _store(tmp_path)
    holder.append(store)
    rebuild_index(_ctx(store, "ds_a", "ds_b"))
    before_a = _entries(store, "ds_a")
    outcomes["ds_a"] = RuntimeError("connection refused")
    outcomes["ds_b"] = "v2"

    result = rebuild_index(_ctx(store, "ds_a", "ds_b"))

    assert result.ok is False
    assert [e["datasource_id"] for e in result.errors] == ["ds_a"]
    assert _entries(store, "ds_a") == before_a
    assert {dict(e[2])["schema_version"] for e in _entries(store, "ds_b")} == {"v2"}


def test_an_unknown_datasource_is_an_error_not_a_silent_no_op(tmp_path, scripted):
    store = _store(tmp_path)

    result = rebuild_index(_ctx(store, "ds_a"), datasource_ids=["nope"])

    assert result.ok is False
    assert "nope" in result.errors[0]["datasource_id"]


def test_rebuild_refuses_a_model_change_without_full(tmp_path, scripted):
    _, holder, _ = scripted
    _index(_store(tmp_path), "ds_a")
    other = _store(tmp_path, embeddings=_ModelB(size=8))
    holder.append(other)

    with pytest.raises(EmbeddingModelMismatchError):
        rebuild_index(_ctx(other, "ds_a"))


def test_a_full_rebuild_switches_every_datasource_to_the_new_model(tmp_path, scripted):
    _, holder, _ = scripted
    _index(_store(tmp_path), "ds_a")
    other = _store(tmp_path, embeddings=_ModelB(size=8))
    holder.append(other)

    result = rebuild_index(_ctx(other, "ds_a", "ds_b"), full=True)

    assert result.ok
    assert other.recorded_embedding_model() == "_ModelB/model-b"
    assert sorted(d.metadata["datasource_id"] for d in other.retrieve_datasource_candidates("q", k=5)) == ["ds_a", "ds_b"]
    names = sorted(c.name for c in other.vectorstore._client.list_collections())
    assert names == ["nl2sql_store"]


def test_a_full_rebuild_that_fails_leaves_the_live_collection_alone(tmp_path, scripted):
    outcomes, holder, _ = scripted
    store = _store(tmp_path)
    holder.append(store)
    rebuild_index(_ctx(store, "ds_a", "ds_b"))
    before = _entries(store, "ds_a") + _entries(store, "ds_b")
    outcomes["ds_b"] = RuntimeError("boom")

    result = rebuild_index(_ctx(store, "ds_a", "ds_b"), full=True)

    assert result.ok is False
    assert _entries(store, "ds_a") + _entries(store, "ds_b") == before
    assert sorted(c.name for c in store.vectorstore._client.list_collections()) == ["nl2sql_store"]


def test_rebuild_passes_the_enrichment_choice_and_reports_steps(tmp_path, scripted):
    _, holder, orchestrator = scripted
    store = _store(tmp_path)
    holder.append(store)
    steps = []

    rebuild_index(_ctx(store, "ds_a"), enrich=False, on_progress=steps.append)

    assert ("enrich", False) in orchestrator.seen
    assert steps[0].startswith("Loading the embedding model")
    assert any("ds_a" in s for s in steps)


def test_retrieved_entries_do_not_carry_the_build_id(tmp_path):
    """The resolver prints a datasource entry's metadata into the decomposer
    prompt; a build id there would change every prompt on every rebuild, and
    replay recordings and the plan cache key on the prompt."""
    store = _store(tmp_path)
    _index(store, "ds_a")

    docs = store.retrieve_datasource_candidates("q", k=5) + store.retrieve_schema_context("q", "ds_a")

    assert docs and all("build_id" not in d.metadata for d in docs)
