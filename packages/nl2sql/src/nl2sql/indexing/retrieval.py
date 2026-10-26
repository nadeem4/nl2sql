"""Retrieval on the index store: MMR searches over one namespace's live builds.

A :class:`Retriever` reads one ``IndexStore`` namespace through a fixed set of
``BuildPointer`` s, one per datasource: a run pins them when it starts
(``Runtime.pin_index``), so every step of the run reads the same build and the
same schema version even if a rebuild switches a pointer meanwhile. The
engine embeds the question with its own embedder and ranks in numpy; the
store only filters and returns the nearest entries with their vectors.

Every search but the verified-query one is maximal marginal relevance (MMR):
embed the query, fetch the ``fetch_k`` nearest entries (the pool), then pick
``k`` of them one at a time. The first pick is the entry most similar to the
query; every later pick maximises

    lambda_mult * similarity(entry, query) - (1 - lambda_mult) * max similarity(entry, already picked)

so an entry that repeats what is already picked loses to a slightly less
relevant one that adds something new. There is no re-ranking model. Each
search can append a record of what it saw and how it chose (the query, the
pool with scores, the picks in order and what was dropped) to an ``explain``
list for the run trace.

See docs/architecture/phase2.md, section 4.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np
from langchain_core.documents import Document

from nl2sql.indexing.models import RouteCandidate, TermCandidate, VerifiedQueryCandidate
from nl2sql_sdk.stores import BuildPointer, Filter, Hit

if TYPE_CHECKING:
    from nl2sql.embeddings import EmbeddingInfo
    from nl2sql_sdk.schema import SchemaSnapshot
    from nl2sql_sdk.stores import IndexStore

_ROUND = 4

CURATED_TYPE = "curated.verified_query"
TERM_TYPE = "knowledge.term"

# Routing's signals, in the order they are searched and recorded, and the entry
# type each one searches. Each ranks datasources by their best entry.
ROUTE_SIGNALS: Dict[str, str] = {
    "description": "schema.datasource",
    "examples": "schema.datasource_example",
    "curated": CURATED_TYPE,
    "tables": "schema.table",
    "columns": "schema.column",
}


def fuse(rankings: Mapping[str, Sequence[str]], rrf_k: int, datasource_ids: Sequence[str],
         similarity: Optional[Mapping[str, float]] = None) -> List[RouteCandidate]:
    """Reciprocal rank fusion over ``datasource_ids``: ``score(ds) = sum over signals of 1 / (rrf_k + rank)``.

    ``rankings`` maps a signal to the datasources it ranked, best first. A
    datasource a signal did not rank (it has no entry of that type) gets the
    rank after the last one ranked, so having entries of a type is not a vote:
    only ranking well in a signal is. A signal that ranked nothing is skipped.
    ``ranks`` records the real ranks only. ``similarity`` is each datasource's
    best entry similarity, carried for :func:`shortlist`'s floor. Best first;
    ties go to the smaller id.
    """
    ids = list(dict.fromkeys(datasource_ids))
    scores: Dict[str, float] = {ds: 0.0 for ds in ids}
    ranks: Dict[str, Dict[str, int]] = {ds: {} for ds in ids}
    for signal, ranked in rankings.items():
        ranked = [ds for ds in ranked if ds in scores]
        if not ranked:
            continue
        for rank, datasource_id in enumerate(ranked, start=1):
            scores[datasource_id] += 1.0 / (rrf_k + rank)
            ranks[datasource_id][signal] = rank
        absent = 1.0 / (rrf_k + len(ranked) + 1)
        for datasource_id in ids:
            if signal not in ranks[datasource_id]:
                scores[datasource_id] += absent
    similarity = similarity or {}
    order = sorted(ids, key=lambda ds: (-scores[ds], ds))
    return [RouteCandidate(datasource_id=ds, score=scores[ds], ranks=ranks[ds],
                           similarity=float(similarity.get(ds, 0.0))) for ds in order]


def shortlist(fused: Sequence[RouteCandidate], limit: int,
              floor_ratio: float) -> Tuple[List[RouteCandidate], List[Dict[str, str]]]:
    """The top ``limit`` of ``fused``, less any datasource far less similar than the best.

    The floor is on similarity, not on the fused score: with a handful of
    datasources every RRF score is within a few percent of the best, so a
    floor on it would never drop anything. A datasource whose best entry
    similarity is under ``floor_ratio`` times the most similar datasource's is
    dropped (no floor when that similarity is not positive, or with
    ``floor_ratio`` 0). Returns the kept
    candidates and, for the trace, each dropped datasource with why:
    ``floor`` or ``limit``.
    """
    if not fused:
        return [], []
    top = max(c.similarity for c in fused)
    floor = floor_ratio * top if top > 0 and floor_ratio > 0 else float("-inf")
    kept: List[RouteCandidate] = []
    dropped: List[Dict[str, str]] = []
    for candidate in fused:
        if candidate.similarity < floor:
            dropped.append({"datasource_id": candidate.datasource_id, "reason": "floor"})
        elif len(kept) >= limit:
            dropped.append({"datasource_id": candidate.datasource_id, "reason": "limit"})
        else:
            kept.append(candidate)
    return kept, dropped


def namespace_for(info: "EmbeddingInfo") -> str:
    """The index namespace of an embedding model: ``nl2sql__<model slug>__<dimension>``.

    The slug is the provider and model, lower case, with every run of other
    characters than letters, digits and dots turned into one hyphen:
    ``nl2sql__local-all-minilm-l6-v2__384``. Each model has its own
    namespace, so changing the model opens an empty one, and changing back
    finds the old one still built.
    """
    slug = re.sub(r"[^a-z0-9.]+", "-", f"{info.provider}-{info.model}".lower()).strip("-.")
    return f"nl2sql__{slug}__{info.dimension}"


def cosine_similarity(x: Sequence[Sequence[float]], y: Sequence[Sequence[float]]) -> np.ndarray:
    """Row-wise cosine similarity; a zero vector scores 0."""
    if len(x) == 0 or len(y) == 0:
        return np.array([])
    x, y = np.array(x), np.array(y)
    x_norm, y_norm = np.linalg.norm(x, axis=1), np.linalg.norm(y, axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        similarity = np.dot(x, y.T) / np.outer(x_norm, y_norm)
    similarity[np.isnan(similarity) | np.isinf(similarity)] = 0.0
    return similarity


def maximal_marginal_relevance(query: Sequence[float], vectors: Sequence[Sequence[float]],
                               lambda_mult: float, k: int) -> List[int]:
    """The indexes of ``vectors`` MMR picks, in pick order."""
    if min(k, len(vectors)) <= 0:
        return []
    to_query = cosine_similarity([query], vectors)[0]
    picks = [int(np.argmax(to_query))]
    picked = np.array([vectors[picks[0]]])
    while len(picks) < min(k, len(vectors)):
        best_score, best = -np.inf, -1
        to_picked = cosine_similarity(vectors, picked)
        for i, query_score in enumerate(to_query):
            if i in picks:
                continue
            score = lambda_mult * query_score - (1 - lambda_mult) * max(to_picked[i])
            if score > best_score:
                best_score, best = score, i
        picks.append(best)
        picked = np.append(picked, [vectors[best]], axis=0)
    return picks


def entry_label(metadata: Mapping[str, Any]) -> str:
    """A short readable name for an index entry: the datasource, table, column or join."""
    kind = metadata.get("type") or ""
    if kind in ("schema.datasource", "schema.datasource_example"):
        return str(metadata.get("datasource_id") or "")
    if kind == "schema.column":
        return f"{metadata.get('table')}.{metadata.get('column')}"
    if kind == "schema.relationship":
        return f"{metadata.get('from_table')} -> {metadata.get('to_table')}"
    if kind == "schema.metric":
        return str(metadata.get("name") or "")
    if kind == CURATED_TYPE:
        return str(metadata.get("query_id") or "")
    if kind == TERM_TYPE:
        return str(metadata.get("term") or "")
    return str(metadata.get("table") or metadata.get("id") or "")


def _public(metadata: Mapping[str, Any]) -> Dict[str, Any]:
    """An entry's metadata as the pipeline reads it.

    The storage-only ``build_id`` is dropped: it would change every prompt the
    metadata is printed into on every rebuild. A relationship's column lists
    are stored joined (an index entry holds scalars only) and split here.
    """
    md = {key: value for key, value in metadata.items() if key != "build_id"}
    for key in ("from_columns", "to_columns"):
        if isinstance(md.get(key), str):
            md[key] = [c for c in md[key].split(",") if c]
    return md


class Retriever:
    """MMR searches over the builds ``pointers`` name, in ``namespace`` of ``index``.

    ``state`` (the state store) resolves each datasource's live snapshot through its
    pointer (:meth:`snapshot`).
    """

    FETCH_MULTIPLIER = 4
    LAMBDA_MULT = 0.7
    CURATED_FETCH_MULTIPLIER = 10
    # Routing (route): the entries each vote search reads, and the reciprocal
    # rank fusion constant (the value the RRF literature uses). Constants until
    # the routing suite shows a reason to tune them (D28).
    VOTE_K = 20
    RRF_K = 60

    def __init__(self, index: "IndexStore", namespace: str, embeddings: Any,
                 pointers: Mapping[str, BuildPointer], state: Any = None):
        self.index = index
        self.namespace = namespace
        self.embeddings = embeddings
        self.pointers: Dict[str, BuildPointer] = dict(pointers)
        self.state = state

    # ------------------------------------------------------------ the live builds

    def pointer(self, datasource_id: str) -> Optional[BuildPointer]:
        return self.pointers.get(datasource_id)

    def schema_version(self, datasource_id: str) -> Optional[str]:
        """The schema version the datasource's live build was made from; None before its first build."""
        pointer = self.pointers.get(datasource_id)
        return pointer.schema_version if pointer else None

    def snapshot(self, datasource_id: str) -> Optional["SchemaSnapshot"]:
        """The snapshot the datasource's live build was made from; None before its first build."""
        version = self.schema_version(datasource_id)
        if version is None or self.state is None:
            return None
        return self.state.get_snapshot(datasource_id, version)

    def is_empty(self) -> bool:
        """True when no datasource has a live build."""
        return not self.pointers

    def _live(self, datasource_ids: Optional[Sequence[str]] = None) -> Dict[str, List[Any]]:
        """An ``in`` condition on the live builds of ``datasource_ids`` (of every datasource when None)."""
        ids = self.pointers if datasource_ids is None else datasource_ids
        return {"build_id": [self.pointers[ds].build_id for ds in dict.fromkeys(ids) if ds in self.pointers]}

    # ------------------------------------------------------------ MMR

    def _pool(self, embedding: List[float], fetch_k: int, where: Filter, exact: bool) -> List[Hit]:
        """The ``fetch_k`` entries passing ``where`` nearest ``embedding``, nearest first, with vectors.

        ``exact`` scores every entry passing ``where`` instead of asking the
        store's approximate search, which with a filter few entries pass can
        return fewer than exist. For small entry sets only (the datasource
        entries); ties keep id order, and the pool records no distance.
        """
        if not exact:
            return self.index.query(self.namespace, embedding, k=fetch_k, where=where, with_vectors=True)
        entries = self.index.get(self.namespace, where, with_vectors=True)
        if not entries:
            return []
        scores = cosine_similarity([embedding], [e.vector for e in entries])[0]
        order = sorted(range(len(entries)), key=lambda i: (-float(scores[i]), entries[i].id))[:fetch_k]
        return [Hit(id=entries[i].id, text=entries[i].text, metadata=entries[i].metadata, distance=float("nan"),
                    vector=entries[i].vector) for i in order]

    def mmr(self, query: str, *, k: int, fetch_k: int, lambda_mult: float, where: Filter,
            with_text: bool = False, exact: bool = False) -> Tuple[List[Document], Dict[str, Any]]:
        """Runs one MMR search and records it.

        Returns:
            The picked entries as documents in pool order (nearest first), and
            the record: the query, the settings, the pool with scores, the
            picks in the order MMR made them, and what was dropped.
            ``with_text`` also records each entry's embedded text (the
            playground's inspector only; a run trace never carries it).
        """
        embedding = self.embeddings.embed_query(query)
        pool_hits = self._pool(embedding, fetch_k, where, exact)
        vectors = [h.vector for h in pool_hits]
        picks = maximal_marginal_relevance(embedding, vectors, lambda_mult, k) if vectors else []
        relevance = cosine_similarity([embedding], vectors)[0] if vectors else []
        between = cosine_similarity(vectors, vectors) if vectors else []

        pool: List[Dict[str, Any]] = []
        for i, hit in enumerate(pool_hits):
            md = hit.metadata
            item: Dict[str, Any] = {
                "rank": i + 1,
                "id": md.get("id") or hit.id,
                "label": entry_label(md),
                "type": md.get("type"),
                "datasource_id": md.get("datasource_id"),
                "table": md.get("table"),
                "column": md.get("column"),
                "similarity": round(float(relevance[i]), _ROUND),
                "distance": None if exact else round(hit.distance, _ROUND),
                "picked": i in picks,
                "pick_order": None,
                "mmr_score": None,
                "redundancy": None,
            }
            if with_text:
                item["text"] = hit.text
            pool.append(item)

        for order, i in enumerate(picks):
            # The first pick is simply the most similar entry; each later one is
            # scored against the picks made before it.
            redundancy = max(float(between[i][j]) for j in picks[:order]) if order else 0.0
            score = float(relevance[i]) if order == 0 else \
                lambda_mult * float(relevance[i]) - (1 - lambda_mult) * redundancy
            pool[i].update(pick_order=order + 1, mmr_score=round(score, _ROUND), redundancy=round(redundancy, _ROUND))

        docs = [Document(page_content=hit.text, metadata=_public(hit.metadata), id=hit.id)
                for i, hit in enumerate(pool_hits) if i in picks]
        record = {
            "query": query, "k": k, "fetch_k": fetch_k, "lambda_mult": lambda_mult,
            "pool": pool,
            "picks": [pool[i]["id"] for i in picks],
            "dropped": [item["id"] for item in pool if not item["picked"]],
        }
        return docs, record

    def _search(self, search: str, query: str, where: Filter, k: int,
                explain: Optional[List[Dict[str, Any]]], scope: Dict[str, Any], exact: bool = False) -> List[Document]:
        from nl2sql.common.resilience import VECTOR_BREAKER

        @VECTOR_BREAKER
        def _execute():
            return self.mmr(query, k=k, fetch_k=k * self.FETCH_MULTIPLIER, lambda_mult=self.LAMBDA_MULT,
                            where=where, exact=exact)

        docs, record = _execute()
        if explain is not None:
            explain.append({"search": search, "filter": scope, **record})
        return docs

    # ------------------------------------------------------------ the pipeline's searches

    def route(self, question: str, datasource_ids: Sequence[str],
              explain: Optional[List[Dict[str, Any]]] = None, *,
              limit: int = 3, floor_ratio: float = 0.5,
              allows: Optional[Callable[[Mapping[str, Any]], bool]] = None) -> List[RouteCandidate]:
        """The datasources ``question`` most likely needs, best first (docs/architecture/wave_a.md, section 9).

        Five searches (:data:`ROUTE_SIGNALS`) run over the live builds of
        ``datasource_ids`` only, so a datasource the caller's scope does not
        reach is never searched, let alone ranked. ``allows`` is the caller's
        entry check (the resolver's: a table or column of a table the scope
        hides, or a verified query that reads one, is not allowed): an entry
        it refuses is dropped before anything is ranked or recorded, and the
        searches fetch more until ``VOTE_K`` allowed entries are found or the
        index runs out. Each search ranks datasources by their best entry: the
        description over every datasource entry, exactly; the example
        questions, verified queries, tables and columns over their ``VOTE_K``
        nearest entries. The ranks are fused with reciprocal rank fusion
        (:func:`fuse`, ``RRF_K``), and :func:`shortlist` keeps the top
        ``limit``, less any datasource whose best similarity is under
        ``floor_ratio`` times the most similar one's.

        ``explain`` gets one record per signal (``route.<signal>``: the pool
        by similarity, each datasource's best entry picked) and a ``routing``
        record: each signal's ranking, the fused scores, a pool of the fused
        datasources, the picks and what was dropped and why.
        """
        from nl2sql.common.resilience import VECTOR_BREAKER

        ids = [ds for ds in dict.fromkeys(datasource_ids) if ds in self.pointers]
        if not ids:
            return []
        live = self._live(ids)
        allowed = allows or (lambda _md: True)

        def votes(embedding: List[float], where: Filter) -> List[Any]:
            fetch = self.VOTE_K if allows is None else self.VOTE_K * self.FETCH_MULTIPLIER
            while True:
                hits = self.index.query(self.namespace, embedding, k=fetch, where=where, with_vectors=True)
                visible = [h for h in hits if allowed(h.metadata)]
                if len(visible) >= self.VOTE_K or len(hits) < fetch:
                    return visible
                fetch *= 2

        @VECTOR_BREAKER
        def _execute():
            embedding = self.embeddings.embed_query(question)
            found = {}
            for signal, entry_type in ROUTE_SIGNALS.items():
                where = Filter(eq={"type": entry_type}, **{"in": live})
                # The description search is exact: one entry per datasource, a
                # handful among hundreds, and approximate search under a filter
                # that selective can miss one.
                found[signal] = ([e for e in self.index.get(self.namespace, where, with_vectors=True)
                                  if allowed(e.metadata)]
                                 if signal == "description" else votes(embedding, where))
            return embedding, found

        embedding, found = _execute()
        rankings: Dict[str, List[str]] = {}
        best_similarity: Dict[str, float] = {}
        records: List[Dict[str, Any]] = []
        for signal, entries in found.items():
            scores = cosine_similarity([embedding], [e.vector for e in entries])[0] if entries else []
            order = sorted(range(len(entries)), key=lambda i: (-float(scores[i]), entries[i].id))
            if signal != "description":
                order = order[:self.VOTE_K]
            ranked: List[str] = []
            picks: List[str] = []
            pool: List[Dict[str, Any]] = []
            for rank, i in enumerate(order, start=1):
                md = entries[i].metadata
                datasource_id = md.get("datasource_id")
                best = datasource_id is not None and datasource_id not in ranked
                if best:
                    ranked.append(datasource_id)
                    picks.append(md.get("id") or entries[i].id)
                    best_similarity[datasource_id] = max(best_similarity.get(datasource_id, float("-inf")),
                                                         float(scores[i]))
                pool.append({"rank": rank, "id": md.get("id") or entries[i].id, "label": entry_label(md),
                             "type": md.get("type"), "datasource_id": datasource_id, "table": md.get("table"),
                             "column": md.get("column"), "similarity": round(float(scores[i]), _ROUND),
                             "picked": best})
            rankings[signal] = ranked
            k = len(entries) if signal == "description" else self.VOTE_K
            records.append({
                "search": f"route.{signal}", "filter": {"datasource_ids": ids, "types": [ROUTE_SIGNALS[signal]]},
                # A plain similarity ranking: MMR with lambda 1.
                "query": question, "k": k, "fetch_k": k, "lambda_mult": 1.0,
                "pool": pool, "picks": picks, "dropped": [p["id"] for p in pool if not p["picked"]],
            })

        fused = fuse(rankings, self.RRF_K, ids, best_similarity)
        kept, dropped = shortlist(fused, limit, floor_ratio)
        if explain is not None:
            picked = {c.datasource_id for c in kept}
            explain.extend(records)
            explain.append({
                "search": "routing", "query": question, "rrf_k": self.RRF_K, "vote_k": self.VOTE_K,
                "candidates": limit, "floor_ratio": floor_ratio,
                "signals": rankings,
                "fused": [{"datasource_id": c.datasource_id, "score": round(c.score, 6), "ranks": c.ranks,
                           "similarity": round(c.similarity, _ROUND)} for c in fused],
                # The fused ranking as a pool, so a search view can draw it.
                "k": limit, "fetch_k": len(fused), "lambda_mult": 1.0,
                "pool": [{"rank": rank, "id": c.datasource_id, "label": c.datasource_id, "type": "schema.datasource",
                          "datasource_id": c.datasource_id, "similarity": round(c.similarity, _ROUND),
                          "score": round(c.score, 6), "picked": c.datasource_id in picked}
                         for rank, c in enumerate(fused, start=1)],
                "picks": [c.datasource_id for c in kept],
                "dropped": dropped,
            })
        return kept

    @staticmethod
    def _tables(tables: Optional[List[str]]) -> Dict[str, List[Any]]:
        return {"table": list(tables)} if tables is not None else {}

    @staticmethod
    def _filter_record(datasource_id: str, types: List[str], tables: Optional[List[str]]) -> Dict[str, Any]:
        """The search's filter, for the trace, with how many tables it was limited to."""
        record: Dict[str, Any] = {"datasource_id": datasource_id, "types": types}
        if tables is not None:
            record["searched_tables"] = len(tables)
        return record

    def retrieve_schema_context(self, query: str, datasource_id: str, k: int = 8,
                                explain: Optional[List[Dict[str, Any]]] = None,
                                tables: Optional[List[str]] = None) -> List[Document]:
        """The datasource's table and metric entries most like ``query``; only ``tables`` when given."""
        types = ["schema.table", "schema.metric"]
        where = Filter(eq={"datasource_id": datasource_id},
                       **{"in": {"type": types, **self._tables(tables), **self._live([datasource_id])}})
        return self._search("tables", query, where, k, explain, self._filter_record(datasource_id, types, tables))

    def retrieve_column_candidates(self, query: str, datasource_id: str, k: int = 8,
                                   explain: Optional[List[Dict[str, Any]]] = None,
                                   tables: Optional[List[str]] = None) -> List[Document]:
        """The datasource's column entries most like ``query``; only ``tables``' columns when given."""
        where = Filter(eq={"datasource_id": datasource_id, "type": "schema.column"},
                       **{"in": {**self._tables(tables), **self._live([datasource_id])}})
        return self._search("columns", query, where, k, explain,
                            self._filter_record(datasource_id, ["schema.column"], tables))

    def retrieve_planning_context(self, query: str, datasource_id: str, tables: List[str], k: int = 12,
                                  explain: Optional[List[Dict[str, Any]]] = None) -> List[Document]:
        """The column and relationship entries of ``tables`` most like ``query``."""
        types = ["schema.column", "schema.relationship"]
        where = Filter(eq={"datasource_id": datasource_id},
                       **{"in": {"type": types, "table": list(tables), **self._live([datasource_id])}})
        return self._search("planning", query, where, k, explain,
                            {"datasource_id": datasource_id, "types": types, "tables": list(tables)})

    def retrieve_verified_query_candidates(self, query: str, datasource_ids: List[str], k: int,
                                           explain: Optional[List[Dict[str, Any]]] = None
                                           ) -> List[VerifiedQueryCandidate]:
        """The verified queries a question most resembles, one per query, best first.

        A plain similarity search, not MMR: several phrasings of one query are
        exactly what should match. The pool is ``k * CURATED_FETCH_MULTIPLIER``
        entries of the live builds of ``datasource_ids``; each query keeps its
        best phrasing's cosine similarity. When none of the datasources has a
        verified query entry, nothing is embedded.
        """
        from nl2sql.common.resilience import VECTOR_BREAKER

        if not datasource_ids:
            return []
        ids = list(dict.fromkeys(datasource_ids))
        where = Filter(eq={"type": CURATED_TYPE}, **{"in": {"datasource_id": ids, **self._live(ids)}})
        available = len(self.index.get(self.namespace, where))
        if available == 0:
            return []
        fetch_k = min(available, k * self.CURATED_FETCH_MULTIPLIER)

        @VECTOR_BREAKER
        def _execute():
            embedding = self.embeddings.embed_query(query)
            return embedding, self.index.query(self.namespace, embedding, k=fetch_k, where=where, with_vectors=True)

        embedding, hits = _execute()
        scores = cosine_similarity([embedding], [h.vector for h in hits])[0] if hits else []

        best: Dict[tuple, VerifiedQueryCandidate] = {}
        best_entry: Dict[tuple, str] = {}
        pool = []
        for i, hit in enumerate(hits):
            md, score = hit.metadata, float(scores[i])
            key = (md.get("datasource_id"), md.get("query_id"))
            pool.append({"id": md.get("id"), "label": md.get("query_id"), "type": md.get("type"),
                         "datasource_id": md.get("datasource_id"), "similarity": round(score, _ROUND)})
            if key not in best or score > best[key].score:
                best[key] = VerifiedQueryCandidate(datasource_id=key[0], query_id=key[1], score=score, text=hit.text)
                best_entry[key] = md.get("id")
        ranked = sorted(best.values(), key=lambda c: c.score, reverse=True)[:k]

        if explain is not None:
            pool.sort(key=lambda item: item["similarity"], reverse=True)
            picks = [best_entry[(c.datasource_id, c.query_id)] for c in ranked]
            for rank, item in enumerate(pool, start=1):
                item["rank"] = rank
                item["picked"] = item["id"] in picks
            explain.append({
                "search": "verified_queries",
                "filter": {"datasource_ids": ids, "types": [CURATED_TYPE]},
                "query": query, "k": k, "fetch_k": fetch_k,
                "pool": pool, "picks": picks,
                "dropped": [item["id"] for item in pool if not item["picked"]],
            })
        return ranked

    def retrieve_term_candidates(self, query: str, datasource_id: str, k: int) -> List[TermCandidate]:
        """The glossary terms of ``datasource_id`` whose entries are most like ``query``, best first.

        A plain similarity search over the live build's ``knowledge.term``
        entries. The caller keeps only the terms its scope shows and the
        ones at or above ``settings.knowledge.match_floor``. When the
        datasource has no term entry, nothing is embedded.
        """
        from nl2sql.common.resilience import VECTOR_BREAKER

        where = Filter(eq={"type": TERM_TYPE, "datasource_id": datasource_id},
                       **{"in": self._live([datasource_id])})
        available = len(self.index.get(self.namespace, where))
        if available == 0:
            return []

        @VECTOR_BREAKER
        def _execute():
            embedding = self.embeddings.embed_query(query)
            return embedding, self.index.query(self.namespace, embedding, k=min(available, k), where=where,
                                               with_vectors=True)

        embedding, hits = _execute()
        scores = cosine_similarity([embedding], [h.vector for h in hits])[0] if hits else []
        found = [TermCandidate(datasource_id=datasource_id, term=str(hit.metadata.get("term")), score=float(scores[i]))
                 for i, hit in enumerate(hits)]
        return sorted(found, key=lambda c: c.score, reverse=True)

    def inspect(self, query: str, k: int = 8, lambda_mult: float = LAMBDA_MULT, types: Optional[List[str]] = None,
                datasource_id: Optional[str] = None, fetch_k: Optional[int] = None) -> Dict[str, Any]:
        """One MMR search over any entry types, for the playground's inspector.

        The same search the engine runs, with the knobs exposed and each
        entry's embedded text included. It reads the live builds only.
        """
        fetch_k = fetch_k or k * self.FETCH_MULTIPLIER
        live = self._live([datasource_id] if datasource_id else None)
        where = Filter(eq={"datasource_id": datasource_id} if datasource_id else {},
                       **{"in": {**({"type": list(types)} if types else {}), **live}})
        _, record = self.mmr(query, k=k, fetch_k=fetch_k, lambda_mult=lambda_mult, where=where, with_text=True)
        return {"filter": {"datasource_id": datasource_id, "types": list(types or [])}, **record}
