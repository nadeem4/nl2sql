"""Re-indexing never leaves an empty or half-built index, and never touches
another datasource's entries.

The old ``nl2sql index`` deleted the whole collection first and rebuilt it
after, so any failure in between left 0 entries and every question failed at
the resolver. Now each datasource is rebuilt on its own: its new entries are
written under a new build id, its build pointer is switched only when every
entry is written, and only then are its older builds deleted. Readers read
the builds the pointers name, so they never see a half-built or mixed set.
The build a switch replaces is kept, for runs that pinned it.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import List

import pytest
from index_helpers import Chunk, TestIndex  # same directory
from langchain_core.embeddings import Embeddings, FakeEmbeddings

from nl2sql.indexing import rebuild as rebuild_module
from nl2sql.indexing.rebuild import rebuild_index
from nl2sql_sdk.stores import Filter


def _chunks(ds, version="v1", tables=("T1", "T2", "T3")) -> List[Chunk]:
    return [Chunk(f"Datasource: {ds}", type="schema.datasource", datasource_id=ds, schema_version=version)] + [
        Chunk(f"{ds}.{t}", type="schema.table", datasource_id=ds, schema_version=version, table=f"{ds}.{t}")
        for t in tables
    ]


def _live(index, ds):
    """The datasource's live entries: ``(text, metadata without the build id)``, sorted."""
    pointer = index.index.pointers(index.namespace)[ds]
    entries = index.index.get(index.namespace, Filter(eq={"build_id": pointer.build_id}))
    return sorted((e.text, tuple(sorted((k, v) for k, v in e.metadata.items() if k != "build_id")))
                  for e in entries)


def _stored(index, ds):
    """Every build id on disk for the datasource."""
    return {e.metadata["build_id"] for e in index.index.get(index.namespace, Filter(eq={"datasource_id": ds}))}


# --- one datasource's build ------------------------------------------------------


def test_reindexing_one_datasource_leaves_the_other_byte_identical(tmp_path):
    index = TestIndex(tmp_path, FakeEmbeddings(size=8))
    index.write("ds_a", _chunks("ds_a"))
    index.write("ds_b", _chunks("ds_b"))
    before_b = _live(index, "ds_b")

    index.write("ds_a", _chunks("ds_a", "v2"), schema_version="v2")

    assert _live(index, "ds_b") == before_b
    assert sorted(text for text, _ in _live(index, "ds_a")) == ["Datasource: ds_a", "ds_a.T1", "ds_a.T2", "ds_a.T3"]
    assert all(dict(md)["schema_version"] == "v2" for _, md in _live(index, "ds_a"))


class _FailsAfter(Embeddings):
    """Embeds the first ``ok_calls`` batches, then fails: a build dying midway."""

    def __init__(self, ok_calls):
        self.ok_calls = ok_calls
        self.inner = FakeEmbeddings(size=8)

    def embed_documents(self, texts):
        if self.ok_calls <= 0:
            raise RuntimeError("embedding service went away")
        self.ok_calls -= 1
        return self.inner.embed_documents(texts)

    def embed_query(self, text):
        return self.inner.embed_query(text)


def test_a_reindex_that_fails_midway_leaves_the_previous_build_live(tmp_path, monkeypatch):
    embeddings = _FailsAfter(ok_calls=100)
    index = TestIndex(tmp_path, embeddings)
    index.write("ds_a", _chunks("ds_a"))
    index.write("ds_b", _chunks("ds_b"))
    before_a, before_b = _live(index, "ds_a"), _live(index, "ds_b")
    pointer = index.index.pointers(index.namespace)["ds_a"]

    monkeypatch.setattr("nl2sql.indexing.orchestrator.BATCH_SIZE", 2)
    embeddings.ok_calls = 1
    with pytest.raises(RuntimeError, match="went away"):
        index.write("ds_a", _chunks("ds_a", "v2", tables=("T1", "T2", "T3", "T4")), schema_version="v2")

    assert index.index.pointers(index.namespace)["ds_a"] == pointer
    assert _live(index, "ds_a") == before_a
    assert _live(index, "ds_b") == before_b
    docs = index.retriever().retrieve_datasource_candidates("anything", k=5)
    assert sorted(d.metadata["datasource_id"] for d in docs) == ["ds_a", "ds_b"]
    assert len(_stored(index, "ds_a")) == 2  # the partial build, named by no pointer

    # The next build of the datasource deletes it.
    embeddings.ok_calls = 100
    index.write("ds_a", _chunks("ds_a", "v3"), schema_version="v3")
    assert len(_stored(index, "ds_a")) == 2  # the new build and the one it replaced


def test_readers_never_see_both_builds_while_they_coexist(tmp_path):
    index = TestIndex(tmp_path, FakeEmbeddings(size=8))
    index.write("ds_a", _chunks("ds_a"))
    seen = {}
    set_pointers = index.index.set_pointers

    def peek(namespace, updates):
        """Just before the switch: the new build is written, not yet live."""
        seen["stored"] = len(index.index.get(namespace, Filter(eq={"datasource_id": "ds_a"})))
        reader = index.retriever()
        seen["resolver"] = reader.retrieve_datasource_candidates("q", k=5)
        seen["tables"] = reader.retrieve_schema_context("q", "ds_a", k=8)
        set_pointers(namespace, updates)

    index.index.set_pointers = peek
    index.write("ds_a", _chunks("ds_a", "v2"), schema_version="v2")

    assert seen["stored"] == 8  # old and new are both on disk at this moment
    assert [d.metadata["schema_version"] for d in seen["resolver"]] == ["v1"]
    assert {d.metadata["schema_version"] for d in seen["tables"]} == {"v1"}
    assert len(seen["tables"]) == 3
    # After the switch only v2 is visible.
    assert [d.metadata["schema_version"] for d in index.retriever().retrieve_datasource_candidates("q", k=5)] \
        == ["v2"]


def test_a_build_keeps_the_one_it_replaced_and_deletes_older_ones(tmp_path):
    index = TestIndex(tmp_path, FakeEmbeddings(size=8))
    first = index.write("ds_a", _chunks("ds_a"))
    second = index.write("ds_a", _chunks("ds_a", "v2"), schema_version="v2")

    assert _stored(index, "ds_a") == {first, second}

    third = index.write("ds_a", _chunks("ds_a", "v3"), schema_version="v3")

    assert _stored(index, "ds_a") == {second, third}


def test_retrieved_entries_do_not_carry_the_build_id(tmp_path):
    """The resolver prints a datasource entry's metadata into the decomposer
    prompt; a build id there would change every prompt on every rebuild, and
    the plan cache keys on the prompt."""
    index = TestIndex(tmp_path, FakeEmbeddings(size=8))
    index.write("ds_a", _chunks("ds_a"))
    reader = index.retriever()

    docs = reader.retrieve_datasource_candidates("q", k=5) + reader.retrieve_schema_context("q", "ds_a")

    assert docs and all("build_id" not in d.metadata for d in docs)


# --- rebuild_index: the entry point for the CLI, the demo and the playground ------


class _Adapter:
    def __init__(self, datasource_id):
        self.datasource_id = datasource_id


def _ctx(index, *datasource_ids, unavailable=None):
    ctx = SimpleNamespace(**vars(index.ctx))
    ctx.ds_registry = SimpleNamespace(list_adapters=lambda: [_Adapter(d) for d in datasource_ids],
                                      unavailable=lambda: dict(unavailable or {}))
    return ctx


@pytest.fixture
def scripted(monkeypatch):
    """An orchestrator that commits a small chunk set per datasource, or raises."""
    outcomes: dict = {}

    class _Orchestrator:
        seen = []

        def __init__(self, ctx, enrich=True):
            _Orchestrator.seen.append(("enrich", enrich))
            self.ctx = ctx

        def index_datasource(self, adapter):
            ds = adapter.datasource_id
            outcome = outcomes.get(ds, "v1")
            if isinstance(outcome, Exception):
                raise outcome
            from nl2sql.indexing.orchestrator import IndexingOrchestrator

            IndexingOrchestrator(self.ctx)._commit(ds, outcome, _chunks(ds, outcome), [], None)
            return {"datasource_id": ds, "schema_version": outcome, "schema.table": 3}

    monkeypatch.setattr(rebuild_module, "IndexingOrchestrator", _Orchestrator)
    return outcomes, _Orchestrator


def test_rebuilding_one_datasource_does_not_clear_the_others(tmp_path, scripted):
    outcomes, _ = scripted
    index = TestIndex(tmp_path, FakeEmbeddings(size=8))
    rebuild_index(_ctx(index, "ds_a", "ds_b"))
    before_b = _live(index, "ds_b")
    outcomes["ds_a"] = "v2"

    result = rebuild_index(_ctx(index, "ds_a", "ds_b"), datasource_ids=["ds_a"])

    assert result.ok
    assert _live(index, "ds_b") == before_b
    assert {dict(md)["schema_version"] for _, md in _live(index, "ds_a")} == {"v2"}


def test_one_failing_datasource_keeps_its_previous_entries_and_the_others_still_rebuild(tmp_path, scripted):
    outcomes, _ = scripted
    index = TestIndex(tmp_path, FakeEmbeddings(size=8))
    rebuild_index(_ctx(index, "ds_a", "ds_b"))
    before_a = _live(index, "ds_a")
    outcomes["ds_a"] = RuntimeError("connection refused")
    outcomes["ds_b"] = "v2"

    result = rebuild_index(_ctx(index, "ds_a", "ds_b"))

    assert result.ok is False
    assert [e["datasource_id"] for e in result.errors] == ["ds_a"]
    assert _live(index, "ds_a") == before_a
    assert {dict(md)["schema_version"] for _, md in _live(index, "ds_b")} == {"v2"}


def test_an_unknown_datasource_is_an_error_not_a_silent_no_op(tmp_path, scripted):
    index = TestIndex(tmp_path, FakeEmbeddings(size=8))

    result = rebuild_index(_ctx(index, "ds_a"), datasource_ids=["nope"])

    assert result.ok is False
    assert "nope" in result.errors[0]["datasource_id"]


def test_rebuild_passes_the_enrichment_choice_and_reports_steps(tmp_path, scripted):
    _, orchestrator = scripted
    index = TestIndex(tmp_path, FakeEmbeddings(size=8))
    steps = []

    rebuild_index(_ctx(index, "ds_a"), enrich=False, on_progress=steps.append)

    assert ("enrich", False) in orchestrator.seen
    assert steps[0].startswith("Loading the embedding model")
    assert any("ds_a" in s for s in steps)


def test_indexing_every_datasource_indexes_the_rest_and_reports_the_unavailable_one(tmp_path, scripted):
    index = TestIndex(tmp_path, FakeEmbeddings(size=8))

    result = rebuild_index(_ctx(index, "ds_a", unavailable={"bad": "driver missing"}))

    assert result.ok is False
    assert [e["datasource_id"] for e in result.errors] == ["bad"]
    assert "driver missing" in result.errors[0]["error"]
    assert _live(index, "ds_a")
