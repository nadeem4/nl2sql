"""Datasource candidates come from every datasource entry, not an approximate search.

There is one ``schema.datasource`` entry per datasource, a handful among
hundreds of table, column and verified-query entries. Chroma's nearest-neighbour
search with a ``where`` filter is approximate (HNSW), and with so few entries
passing the filter it can return fewer than exist: a datasource then silently
drops out of the candidates. Since the resolver forwards only what the
answerability verdict names among the candidates, a missing candidate refuses a
question its datasource could answer. The datasource search therefore ranks
every datasource entry exactly.
"""
from __future__ import annotations

from langchain_core.documents import Document

from nl2sql.indexing.vector_store import VectorStore

from test_retrieval_trace import BagEmbeddings  # same directory


class LossyCollection:
    """A Chroma collection whose filtered ``query`` misses one entry, as HNSW can."""

    def __init__(self, collection, missing_datasource):
        self._collection = collection
        self._missing = missing_datasource

    def __getattr__(self, name):
        return getattr(self._collection, name)

    def query(self, *args, **kwargs):
        results = self._collection.query(*args, **kwargs)
        keep = [i for i, md in enumerate(results["metadatas"][0]) if md.get("datasource_id") != self._missing]
        return {key: ([[value[0][i] for i in keep]] if isinstance(value, list) and value and value[0] is not None
                      and key in ("ids", "documents", "metadatas", "distances", "embeddings") else value)
                for key, value in results.items()}


def _store(tmp_path) -> VectorStore:
    store = VectorStore("nl2sql_store", str(tmp_path / "vs"), embeddings=BagEmbeddings())
    store.vectorstore.add_documents([
        Document(page_content=text, metadata={"type": "schema.datasource", "datasource_id": ds_id,
                                              "schema_version": "v1", "id": f"ds:{ds_id}"})
        for ds_id, text in [("chinook", "music store sales customer invoice"),
                            ("support", "customer tickets"),
                            ("webanalytics", "customer sessions page views")]
    ] + [
        Document(page_content=f"table {i} customer", metadata={"type": "schema.table", "datasource_id": "chinook",
                                                               "table": f"T{i}", "id": f"t:{i}"})
        for i in range(20)
    ])
    return store


def test_a_datasource_the_approximate_search_misses_is_still_a_candidate(tmp_path, monkeypatch):
    store = _store(tmp_path)
    lossy = LossyCollection(store.vectorstore._collection, "chinook")
    monkeypatch.setattr(type(store.vectorstore), "_collection", property(lambda _self: lossy))

    docs = store.retrieve_datasource_candidates("Top 500 customers by sales in 2012", k=5)

    assert sorted(d.metadata["datasource_id"] for d in docs) == ["chinook", "support", "webanalytics"]


def test_the_exact_ranking_puts_the_nearest_datasource_first_and_records_it(tmp_path):
    store = _store(tmp_path)
    explain: list = []

    docs = store.retrieve_datasource_candidates("music sales invoice", k=5, explain=explain)

    [record] = explain
    assert record["search"] == "datasources"
    assert record["pool"][0]["datasource_id"] == "chinook"
    assert [p["similarity"] for p in record["pool"]] == sorted((p["similarity"] for p in record["pool"]), reverse=True)
    assert {d.metadata["datasource_id"] for d in docs} == {"chinook", "support", "webanalytics"}
