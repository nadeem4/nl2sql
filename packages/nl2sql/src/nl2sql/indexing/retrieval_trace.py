"""One MMR vector search, with a record of what it saw and how it chose.

Every retrieval in the engine is a maximal marginal relevance (MMR) search over
the one Chroma collection: embed the query, fetch the ``fetch_k`` nearest
entries (the pool), then pick ``k`` of them one at a time. The first pick is
the entry most similar to the query; every later pick maximises

    lambda_mult * similarity(entry, query) - (1 - lambda_mult) * max similarity(entry, already picked)

so an entry that repeats what is already picked loses to a slightly less
relevant one that adds something new. There is no re-ranking model.

:func:`mmr_search` is the search the engine runs. It makes the same Chroma
query and calls the same ``maximal_marginal_relevance`` function that
``Chroma.max_marginal_relevance_search`` does, so its documents are exactly
what that method returns (in pool order, nearest first); the record comes from
the numbers MMR already computed, so recording it costs no second search.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from langchain_chroma.vectorstores import cosine_similarity, maximal_marginal_relevance
from langchain_core.documents import Document

_ROUND = 4


def entry_label(metadata: Dict[str, Any]) -> str:
    """A short readable name for an index entry: the datasource, table, column or join."""
    kind = metadata.get("type") or ""
    if kind == "schema.datasource":
        return str(metadata.get("datasource_id") or "")
    if kind == "schema.column":
        return f"{metadata.get('table')}.{metadata.get('column')}"
    if kind == "schema.relationship":
        return f"{metadata.get('from_table')} -> {metadata.get('to_table')}"
    if kind == "schema.metric":
        return str(metadata.get("name") or "")
    return str(metadata.get("table") or metadata.get("id") or "")


def _first(results: Dict[str, Any], key: str) -> List[Any]:
    rows = results.get(key)
    if rows is None or len(rows) == 0 or rows[0] is None:
        return []
    return list(rows[0])


def mmr_search(
    collection: Any,
    embeddings: Any,
    query: str,
    *,
    k: int,
    fetch_k: int,
    lambda_mult: float,
    where: Optional[Dict[str, Any]],
    with_text: bool = False,
) -> Tuple[List[Document], Dict[str, Any]]:
    """Runs one MMR search and records it.

    Args:
        collection: The Chroma collection.
        embeddings: The embedder the collection was built with.
        query: The text embedded.
        k: How many entries MMR picks.
        fetch_k: How many nearest entries form the pool MMR picks from.
        lambda_mult: Weight on relevance; ``1 - lambda_mult`` weighs difference.
        where: The Chroma metadata filter.
        with_text: Also record each entry's embedded text (the playground's
            inspector only; a run trace never carries it).

    Returns:
        The picked documents in pool order (as Chroma returns them), and the
        record: the query, the settings, the pool with scores, the picks in
        the order MMR made them, and what was dropped.
    """
    embedding = embeddings.embed_query(query)
    results = collection.query(
        query_embeddings=[embedding],
        n_results=fetch_k,
        where=where,
        include=["metadatas", "documents", "distances", "embeddings"],
    )
    ids = _first(results, "ids")
    texts = _first(results, "documents")
    metadatas = _first(results, "metadatas")
    distances = _first(results, "distances")
    vectors = _first(results, "embeddings")

    picks: List[int] = (
        maximal_marginal_relevance(np.array(embedding, dtype=np.float32), vectors, k=k, lambda_mult=lambda_mult)
        if vectors
        else []
    )
    relevance = cosine_similarity([embedding], vectors)[0] if vectors else []
    between = cosine_similarity(vectors, vectors) if vectors else []

    pool: List[Dict[str, Any]] = []
    for i, entry_id in enumerate(ids):
        md = dict(metadatas[i] or {}) if i < len(metadatas) else {}
        item: Dict[str, Any] = {
            "rank": i + 1,
            "id": md.get("id") or entry_id,
            "label": entry_label(md),
            "type": md.get("type"),
            "datasource_id": md.get("datasource_id"),
            "table": md.get("table"),
            "column": md.get("column"),
            "similarity": round(float(relevance[i]), _ROUND),
            "distance": round(float(distances[i]), _ROUND) if i < len(distances) else None,
            "picked": i in picks,
            "pick_order": None,
            "mmr_score": None,
            "redundancy": None,
        }
        if with_text:
            item["text"] = texts[i] if i < len(texts) else None
        pool.append(item)

    for order, i in enumerate(picks):
        # The first pick is simply the most similar entry; each later one is
        # scored against the picks made before it.
        redundancy = max(float(between[i][j]) for j in picks[:order]) if order else 0.0
        score = float(relevance[i]) if order == 0 else lambda_mult * float(relevance[i]) - (1 - lambda_mult) * redundancy
        pool[i].update(pick_order=order + 1, mmr_score=round(score, _ROUND), redundancy=round(redundancy, _ROUND))

    docs = [
        Document(page_content=texts[i], metadata=dict(metadatas[i] or {}), id=ids[i])
        for i in range(len(ids))
        if i in picks and texts[i] is not None
    ]
    record = {
        "query": query,
        "k": k,
        "fetch_k": fetch_k,
        "lambda_mult": lambda_mult,
        "pool": pool,
        "picks": [pool[i]["id"] for i in picks],
        "dropped": [item["id"] for item in pool if not item["picked"]],
    }
    return docs, record
