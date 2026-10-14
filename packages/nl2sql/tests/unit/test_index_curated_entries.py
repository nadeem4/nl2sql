"""Curated entries live in their datasource's build, and candidates come from them.

A build replaces every entry of its datasource, so curated entries written on
their own would be deleted by the next schema re-index. They are written in
the same build. A ``curated.yaml`` change needs no schema re-read and no model
call: ``IndexingOrchestrator.refresh_curated`` copies the live build's schema
entries, vectors included, into a new build beside fresh curated entries and
switches the pointer to it, keeping its schema version.
"""
from __future__ import annotations

import math
import re
from typing import List

import pytest
from index_helpers import Chunk, TestIndex  # same directory
from langchain_core.embeddings import Embeddings

from nl2sql.config import Curated
from nl2sql.config.curated import CuratedDatasource
from nl2sql.indexing.curated import CuratedChunkBuilder, curated_hash
from nl2sql.indexing.orchestrator import IndexingOrchestrator
from nl2sql_sdk.stores import Entry, Filter

VOCAB = ["customer", "revenue", "best", "top", "track", "genre", "artist", "album", "invoice", "employee",
         "datasource", "table", "year"]


class _Words(Embeddings):
    """A bag of known words, normalised: similarity is word overlap."""

    model = "words"

    def _vec(self, text: str) -> List[float]:
        words = re.findall(r"[a-z]+", text.lower())
        v = [float(sum(1 for w in words if w.startswith(term))) for term in VOCAB] + [0.01]
        norm = math.sqrt(sum(x * x for x in v))
        return [x / norm for x in v]

    def embed_documents(self, texts):
        return [self._vec(t) for t in texts]

    def embed_query(self, text):
        return self._vec(text)


def _schema(ds, version="v1"):
    return [Chunk(f"datasource {ds}", type="schema.datasource", datasource_id=ds, schema_version=version),
            Chunk("table Customer", type="schema.table", datasource_id=ds, schema_version=version),
            Chunk("table Track", type="schema.table", datasource_id=ds, schema_version=version)]


def _section(*queries) -> CuratedDatasource:
    return CuratedDatasource.model_validate({"verified_queries": list(queries)})


TOP_CUSTOMERS = {
    "id": "top_customers", "description": "Customers ranked by revenue",
    "questions": ["Top customers by revenue", "Best customers"], "sql": "SELECT 1",
}
TRACKS_BY_GENRE = {
    "id": "tracks_by_genre", "description": "Tracks per genre",
    "questions": ["How many tracks per genre"], "sql": "SELECT 1",
}


def _store(tmp_path, embeddings=None):
    return TestIndex(tmp_path, embeddings or _Words())


def _index(store, ds, section, version="v1"):
    chunks = _schema(ds, version) + CuratedChunkBuilder(ds, section, version).build()
    store.write(ds, chunks, schema_version=version, curated_hash=curated_hash(section))


def _pointer(store, ds):
    return store.index.pointers(store.namespace)[ds]


def _active(store, ds, kind=None) -> list:
    where = Filter(eq={"build_id": _pointer(store, ds).build_id, **({"type": kind} if kind else {})})
    return store.index.get(store.namespace, where, with_vectors=True)


def _all(store, ds) -> list:
    return store.index.get(store.namespace, Filter(eq={"datasource_id": ds}))


def _schema_entries(store, ds):
    entries = store.index.get(store.namespace, Filter(eq={"build_id": _pointer(store, ds).build_id},
                                                      ne={"type": "curated.verified_query"}), with_vectors=True)
    return sorted(
        (e.text, tuple(round(float(x), 6) for x in e.vector),
         tuple(sorted((k, v) for k, v in e.metadata.items() if k != "build_id")))
        for e in entries
    )


def _refresh(store, ds, section):
    """The curated-only refresh, as ``nl2sql index --curated-only`` runs it."""
    store.ctx.curated = Curated.model_validate({"datasources": {ds: section.model_dump()}}) if section else None
    return IndexingOrchestrator(store.ctx).refresh_curated(ds)


def test_curated_entries_are_written_in_the_schema_build(tmp_path):
    store = _store(tmp_path)
    _index(store, "chinook", _section(TOP_CUSTOMERS))

    got = _active(store, "chinook", "curated.verified_query")
    assert sorted(e.text for e in got) == ["Best customers", "Customers ranked by revenue", "Top customers by revenue"]
    assert _pointer(store, "chinook").curated_hash == curated_hash(_section(TOP_CUSTOMERS))


def test_a_schema_reindex_keeps_the_curated_entries(tmp_path):
    store = _store(tmp_path)
    section = _section(TOP_CUSTOMERS)
    _index(store, "chinook", section)

    _index(store, "chinook", section, version="v2")

    assert len(_active(store, "chinook", "curated.verified_query")) == 3


def test_candidates_are_one_per_query_best_phrasing_wins(tmp_path):
    store = _store(tmp_path)
    _index(store, "chinook", _section(TOP_CUSTOMERS, TRACKS_BY_GENRE))

    found = store.retriever().retrieve_verified_query_candidates("best customers", ["chinook"], k=5)

    assert [(c.datasource_id, c.query_id) for c in found] == [("chinook", "top_customers"),
                                                              ("chinook", "tracks_by_genre")]
    assert found[0].text == "Best customers"
    assert found[0].score == pytest.approx(1.0, abs=1e-3)
    assert found[0].score > found[1].score


def test_candidates_are_cut_to_k(tmp_path):
    # The curated matcher passes settings.curated.match_top_k; the retriever has no default of its own.
    store = _store(tmp_path)
    _index(store, "chinook", _section(TOP_CUSTOMERS, TRACKS_BY_GENRE))

    assert len(store.retriever().retrieve_verified_query_candidates("customers", ["chinook"], k=1)) == 1


def test_candidates_come_only_from_the_given_datasources(tmp_path):
    store = _store(tmp_path)
    _index(store, "chinook", _section(TOP_CUSTOMERS))
    _index(store, "music", _section(TRACKS_BY_GENRE))

    found = store.retriever().retrieve_verified_query_candidates("tracks per genre", ["chinook"], k=5)

    assert {c.datasource_id for c in found} == {"chinook"}


def test_candidates_come_only_from_the_live_build(tmp_path):
    store = _store(tmp_path)
    _index(store, "chinook", _section(TOP_CUSTOMERS))
    # A build that was written but never switched in, e.g. a crash mid-index.
    stray = CuratedChunkBuilder("chinook", _section(TRACKS_BY_GENRE), "v1").build()
    store.index.add(store.namespace, [
        Entry(id=f"stray{i}", text=c.get_page_content(), vector=store.embeddings.embed_query(c.get_page_content()),
              metadata={**c.get_metadata(), "build_id": "never_switched"})
        for i, c in enumerate(stray)])

    found = store.retriever().retrieve_verified_query_candidates("tracks per genre", ["chinook"], k=5)

    assert [c.query_id for c in found] == ["top_customers"]


def test_no_curated_entries_means_no_embedding_call(tmp_path):
    store = _store(tmp_path)
    _index(store, "chinook", None)
    retriever = store.retriever()

    def _no_query(text):
        raise AssertionError("embedded a query with nothing to search")

    retriever.embeddings.embed_query = _no_query
    assert retriever.retrieve_verified_query_candidates("best customers", ["chinook"], k=5) == []
    assert retriever.retrieve_verified_query_candidates("best customers", [], k=5) == []


def test_the_search_is_recorded_when_asked(tmp_path):
    store = _store(tmp_path)
    _index(store, "chinook", _section(TOP_CUSTOMERS))

    explain = []
    store.retriever().retrieve_verified_query_candidates("best customers", ["chinook"], k=5, explain=explain)

    [record] = explain
    assert record["search"] == "verified_queries"
    assert record["picks"] == ["curated.verified_query:chinook:top_customers:1"]
    assert record["pool"][0]["similarity"] == pytest.approx(1.0, abs=1e-3)


# --- curated-only refresh --------------------------------------------------------


def test_a_curated_only_refresh_keeps_schema_entries_and_vectors_and_swaps_curated(tmp_path):
    store = _store(tmp_path)
    _index(store, "chinook", _section(TOP_CUSTOMERS))
    _index(store, "music", _section(TRACKS_BY_GENRE))
    schema_before = _schema_entries(store, "chinook")
    music_before = sorted(e.id for e in _all(store, "music"))
    old = _pointer(store, "chinook")

    new_section = _section(TRACKS_BY_GENRE)
    stats = _refresh(store, "chinook", new_section)

    new = _pointer(store, "chinook")
    assert new.build_id != old.build_id and new.schema_version == old.schema_version == "v1"
    assert _schema_entries(store, "chinook") == schema_before
    assert sorted(e.text for e in _active(store, "chinook", "curated.verified_query")) == [
        "How many tracks per genre", "Tracks per genre"]
    # The other datasource is untouched.
    assert sorted(e.id for e in _all(store, "music")) == music_before
    assert new.curated_hash == curated_hash(new_section)
    assert stats["curated.verified_query"] == 2 and stats["schema.table"] == 2
    assert stats["schema_version"] == "v1"


def test_a_curated_only_refresh_embeds_only_the_curated_entries(tmp_path):
    embeddings = _Words()
    store = _store(tmp_path, embeddings)
    _index(store, "chinook", None)

    texts = []
    original = embeddings.embed_documents

    def spy(batch):
        texts.extend(batch)
        return original(batch)

    embeddings.embed_documents = spy
    _refresh(store, "chinook", _section(TOP_CUSTOMERS))

    assert sorted(texts) == ["Best customers", "Customers ranked by revenue", "Top customers by revenue"]


def test_a_curated_only_refresh_needs_an_indexed_datasource(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(LookupError, match="nl2sql index"):
        _refresh(store, "chinook", None)


def test_the_live_schema_version_is_the_pointers(tmp_path):
    store = _store(tmp_path)
    _index(store, "chinook", None, version="v7")

    assert store.retriever().schema_version("chinook") == "v7"
    assert store.retriever().schema_version("nope") is None
