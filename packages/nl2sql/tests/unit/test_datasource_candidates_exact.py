"""Datasource candidates come from every datasource entry, not an approximate search.

There is one ``schema.datasource`` entry per datasource, a handful among
hundreds of table, column and verified-query entries. An index store's nearest-neighbour
search with a ``where`` filter is approximate (HNSW), and with so few entries
passing the filter it can return fewer than exist: a datasource then silently
drops out of the candidates. Since the resolver forwards only what the
answerability verdict names among the candidates, a missing candidate refuses a
question its datasource could answer. The datasource search therefore ranks
every datasource entry exactly.
"""
from __future__ import annotations

from index_helpers import Chunk, TestIndex  # same directory
from test_retrieval_trace import BagEmbeddings  # same directory

from nl2sql.indexing.retrieval import Retriever


class LossyIndex:
    """An index store whose filtered ``query`` misses one datasource's entries, as HNSW can."""

    def __init__(self, index, missing_datasource):
        self._index = index
        self._missing = missing_datasource

    def __getattr__(self, name):
        return getattr(self._index, name)

    def query(self, *args, **kwargs):
        return [h for h in self._index.query(*args, **kwargs) if h.metadata.get("datasource_id") != self._missing]


def _store(tmp_path) -> Retriever:
    index = TestIndex(tmp_path, BagEmbeddings())
    datasources = [("chinook", "music store sales customer invoice"), ("support", "customer tickets"),
                   ("webanalytics", "customer sessions page views")]
    for ds_id, text in datasources:
        tables = [Chunk(f"table {i} customer", type="schema.table", datasource_id=ds_id, table=f"T{i}",
                        id=f"t:{ds_id}:{i}") for i in range(20 if ds_id == "chinook" else 0)]
        index.write(ds_id, [Chunk(text, type="schema.datasource", datasource_id=ds_id, schema_version="v1",
                                  id=f"ds:{ds_id}"), *tables])
    return index.retriever()


def test_a_datasource_the_approximate_search_misses_is_still_a_candidate(tmp_path):
    store = _store(tmp_path)
    store.index = LossyIndex(store.index, "chinook")

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
