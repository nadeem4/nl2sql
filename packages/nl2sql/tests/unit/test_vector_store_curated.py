"""Curated entries live in their datasource's build, and candidates come from them.

``refresh_schema_chunks`` replaces every entry of a datasource that is not in
the new build, so curated entries written on their own would be deleted by the
next schema re-index. They are written in the same build. A ``curated.yaml``
change needs no schema re-read and no model call: ``refresh_curated_chunks``
copies the active build's schema entries, vectors included, into a new build
beside fresh curated entries and switches to it.
"""
from __future__ import annotations

import math
import re
from typing import List

import pytest
from langchain_core.embeddings import Embeddings

from nl2sql.configs.curated import CuratedDatasource
from nl2sql.indexing.curated import CuratedChunkBuilder, curated_hash
from nl2sql.indexing.vector_store import VectorStore

VOCAB = ["customer", "revenue", "best", "top", "track", "genre", "artist", "album", "invoice", "employee",
         "datasource", "table", "year"]


class _Words(Embeddings):
    """A bag of known words, normalised: similarity is word overlap."""

    model = "words"

    def __init__(self):
        self.document_calls = 0

    def _vec(self, text: str) -> List[float]:
        words = re.findall(r"[a-z]+", text.lower())
        v = [float(sum(1 for w in words if w.startswith(term))) for term in VOCAB] + [0.01]
        norm = math.sqrt(sum(x * x for x in v))
        return [x / norm for x in v]

    def embed_documents(self, texts):
        self.document_calls += 1
        return [self._vec(t) for t in texts]

    def embed_query(self, text):
        return self._vec(text)


class _Chunk:
    def __init__(self, ds, kind, text, version="v1"):
        self.type = kind
        self._text = text
        self._md = {"type": kind, "datasource_id": ds, "schema_version": version}

    def get_page_content(self):
        return self._text

    def get_metadata(self):
        return dict(self._md)


def _schema(ds, version="v1"):
    return [_Chunk(ds, "schema.datasource", f"datasource {ds}", version),
            _Chunk(ds, "schema.table", "table Customer", version),
            _Chunk(ds, "schema.table", "table Track", version)]


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
    return VectorStore("nl2sql_store", str(tmp_path / "vs"), embeddings=embeddings or _Words())


def _index(store, ds, section, version="v1"):
    chunks = _schema(ds, version) + CuratedChunkBuilder(ds, section, version).build()
    return store.refresh_schema_chunks(ds, version, chunks, [], curated_hash=curated_hash(section))


def _active(store, ds, kind=None):
    clauses = [{"datasource_id": ds}, {"build_id": store.active_builds()[ds]}]
    if kind:
        clauses.append({"type": kind})
    got = store.vectorstore._collection.get(where={"$and": clauses}, include=["documents", "embeddings"])
    return got


def _schema_entries(store, ds):
    got = store.vectorstore._collection.get(
        where={"$and": [{"datasource_id": ds}, {"build_id": store.active_builds()[ds]},
                        {"type": {"$ne": "curated.verified_query"}}]},
        include=["documents", "embeddings", "metadatas"],
    )
    return sorted(
        (doc, tuple(round(float(x), 6) for x in vec), tuple(sorted((k, v) for k, v in md.items() if k != "build_id")))
        for doc, vec, md in zip(got["documents"], got["embeddings"], got["metadatas"])
    )


def test_curated_entries_are_written_in_the_schema_build(tmp_path):
    store = _store(tmp_path)
    stats = _index(store, "chinook", _section(TOP_CUSTOMERS))

    assert stats["curated.verified_query"] == 3
    got = _active(store, "chinook", "curated.verified_query")
    assert sorted(got["documents"]) == ["Best customers", "Customers ranked by revenue", "Top customers by revenue"]
    assert store._live_metadata()["curated_hash:chinook"] == curated_hash(_section(TOP_CUSTOMERS))


def test_a_schema_reindex_keeps_the_curated_entries(tmp_path):
    store = _store(tmp_path)
    section = _section(TOP_CUSTOMERS)
    _index(store, "chinook", section)

    _index(store, "chinook", section, version="v2")

    assert len(_active(store, "chinook", "curated.verified_query")["ids"]) == 3
    # Nothing of the old build is left behind.
    assert len(store.vectorstore._collection.get(where={"datasource_id": "chinook"})["ids"]) == 6


def test_candidates_are_one_per_query_best_phrasing_wins(tmp_path):
    store = _store(tmp_path)
    _index(store, "chinook", _section(TOP_CUSTOMERS, TRACKS_BY_GENRE))

    found = store.retrieve_verified_query_candidates("best customers", ["chinook"], k=5)

    assert [(c.datasource_id, c.query_id) for c in found] == [("chinook", "top_customers"), ("chinook", "tracks_by_genre")]
    assert found[0].text == "Best customers"
    assert found[0].score == pytest.approx(1.0, abs=1e-3)
    assert found[0].score > found[1].score


def test_candidates_are_cut_to_k(tmp_path):
    store = _store(tmp_path)
    _index(store, "chinook", _section(TOP_CUSTOMERS, TRACKS_BY_GENRE))

    assert len(store.retrieve_verified_query_candidates("customers", ["chinook"], k=1)) == 1


def test_candidates_come_only_from_the_given_datasources(tmp_path):
    store = _store(tmp_path)
    _index(store, "chinook", _section(TOP_CUSTOMERS))
    _index(store, "music", _section(TRACKS_BY_GENRE))

    found = store.retrieve_verified_query_candidates("tracks per genre", ["chinook"], k=5)

    assert {c.datasource_id for c in found} == {"chinook"}


def test_candidates_come_only_from_the_active_build(tmp_path):
    store = _store(tmp_path)
    _index(store, "chinook", _section(TOP_CUSTOMERS))
    # A build that was written but never switched in, e.g. a crash mid-index.
    stray = CuratedChunkBuilder("chinook", _section(TRACKS_BY_GENRE), "v1").build()
    docs = store._prepare_chunk_documents(stray)
    for d in docs:
        d.metadata["build_id"] = "never_switched"
    store.vectorstore.add_documents(docs, ids=[f"stray{i}" for i in range(len(docs))])

    found = store.retrieve_verified_query_candidates("tracks per genre", ["chinook"], k=5)

    assert [c.query_id for c in found] == ["top_customers"]


def test_no_curated_entries_means_no_embedding_call(tmp_path):
    store = _store(tmp_path)
    _index(store, "chinook", None)

    def _no_query(text):
        raise AssertionError("embedded a query with nothing to search")

    store.embeddings.embed_query = _no_query
    assert store.retrieve_verified_query_candidates("best customers", ["chinook"], k=5) == []
    assert store.retrieve_verified_query_candidates("best customers", [], k=5) == []


def test_k_defaults_to_the_setting(tmp_path, monkeypatch):
    from nl2sql.common.settings import settings

    store = _store(tmp_path)
    _index(store, "chinook", _section(TOP_CUSTOMERS, TRACKS_BY_GENRE))
    monkeypatch.setattr(settings, "curated_match_top_k", 1)

    assert len(store.retrieve_verified_query_candidates("customers", ["chinook"])) == 1


def test_the_search_is_recorded_when_asked(tmp_path):
    store = _store(tmp_path)
    _index(store, "chinook", _section(TOP_CUSTOMERS))

    explain = []
    store.retrieve_verified_query_candidates("best customers", ["chinook"], k=5, explain=explain)

    [record] = explain
    assert record["search"] == "verified_queries"
    assert record["picks"] == ["curated.verified_query:chinook:top_customers:1"]
    assert record["pool"][0]["similarity"] == pytest.approx(1.0, abs=1e-3)


# --- curated-only refresh --------------------------------------------------------


def test_a_curated_only_refresh_keeps_schema_entries_and_vectors_and_swaps_curated(tmp_path):
    embeddings = _Words()
    store = _store(tmp_path, embeddings)
    _index(store, "chinook", _section(TOP_CUSTOMERS))
    _index(store, "music", _section(TRACKS_BY_GENRE))
    schema_before = _schema_entries(store, "chinook")
    music_before = store.vectorstore._collection.get(where={"datasource_id": "music"})
    old_build = store.active_builds()["chinook"]

    new_section = _section(TRACKS_BY_GENRE)
    chunks = CuratedChunkBuilder("chinook", new_section, "v1").build()
    stats = store.refresh_curated_chunks("chinook", chunks, curated_hash=curated_hash(new_section))

    assert store.active_builds()["chinook"] != old_build
    schema_after = _schema_entries(store, "chinook")
    assert schema_after == schema_before
    assert sorted(_active(store, "chinook", "curated.verified_query")["documents"]) == [
        "How many tracks per genre", "Tracks per genre"]
    # Nothing of the old build is left, and the other datasource is untouched.
    assert len(store.vectorstore._collection.get(where={"datasource_id": "chinook"})["ids"]) == 5
    assert store.vectorstore._collection.get(where={"datasource_id": "music"}) == music_before
    assert store._live_metadata()["curated_hash:chinook"] == curated_hash(new_section)
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
    store.refresh_curated_chunks("chinook", CuratedChunkBuilder("chinook", _section(TOP_CUSTOMERS), "v1").build(),
                                 curated_hash=curated_hash(_section(TOP_CUSTOMERS)))

    assert sorted(texts) == ["Best customers", "Customers ranked by revenue", "Top customers by revenue"]


def test_a_curated_only_refresh_needs_an_indexed_datasource(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(LookupError, match="nl2sql index"):
        store.refresh_curated_chunks("chinook", [], curated_hash=curated_hash(None))


def test_the_active_schema_version_is_read_from_the_index(tmp_path):
    store = _store(tmp_path)
    _index(store, "chinook", None, version="v7")

    assert store.active_schema_version("chinook") == "v7"
    assert store.active_schema_version("nope") is None
