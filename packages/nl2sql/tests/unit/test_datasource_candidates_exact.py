"""Routing's description signal reads every datasource entry, not an approximate search.

There is one ``schema.datasource`` entry per datasource, a handful among
hundreds of table, column and verified-query entries. An index store's nearest-neighbour
search with a ``where`` filter is approximate (HNSW), and with so few entries
passing the filter it can return fewer than exist: a datasource would then
silently lose its description vote. The description search in
``Retriever.route`` therefore ranks every datasource entry exactly, so every
datasource the caller reaches is ranked at least once.
"""
from __future__ import annotations

from index_helpers import Chunk, TestIndex  # same directory
from test_retrieval_trace import BagEmbeddings  # same directory

from nl2sql.indexing.retrieval import Retriever

IDS = ["chinook", "support", "webanalytics"]


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


def test_a_datasource_the_approximate_search_misses_is_still_ranked(tmp_path):
    store = _store(tmp_path)
    store.index = LossyIndex(store.index, "chinook")

    routed = store.route("Top 500 customers by sales in 2012", IDS, floor_ratio=0.0)

    assert sorted(c.datasource_id for c in routed) == IDS
    assert next(c for c in routed if c.datasource_id == "chinook").ranks == {"description": 1}


def test_the_exact_ranking_puts_the_nearest_datasource_first_and_records_it(tmp_path):
    store = _store(tmp_path)
    explain: list = []

    store.route("music sales invoice", IDS, explain=explain, floor_ratio=0.0)

    record = explain[0]
    assert record["search"] == "route.description"
    assert record["pool"][0]["datasource_id"] == "chinook"
    assert [p["similarity"] for p in record["pool"]] == sorted((p["similarity"] for p in record["pool"]), reverse=True)
    assert {p["datasource_id"] for p in record["pool"]} == set(IDS)
