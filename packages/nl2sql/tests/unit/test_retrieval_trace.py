"""What a vector search retrieved, its scores, and how MMR chose among the pool.

The engine has no re-ranking model: every retrieval is an MMR search. These
tests pin that the record of a search is the search the engine ran -- same
documents as ``Chroma.max_marginal_relevance_search``, picks in the order MMR
made them, scores from the same cosine similarity -- and that the record never
carries an entry's embedded text (which holds sample values).
"""
from __future__ import annotations

import math
from typing import List

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings

from nl2sql.indexing.vector_store import VectorStore

VOCAB = ["genre", "track", "sales", "invoice", "customer", "artist", "album", "employee", "name", "price"]


class BagEmbeddings(Embeddings):
    """Deterministic word-count vectors, so similarities are predictable."""

    def _embed(self, text: str) -> List[float]:
        words = text.lower().replace(".", " ").replace(",", " ").split()
        vec = [float(sum(1 for w in words if w.startswith(v))) for v in VOCAB]
        vec.append(0.1)  # never all-zero
        norm = math.sqrt(sum(x * x for x in vec))
        return [x / norm for x in vec]

    def embed_documents(self, texts):
        return [self._embed(t) for t in texts]

    def embed_query(self, text):
        return self._embed(text)


def _doc(kind, text, **md):
    meta = {"type": kind, "datasource_id": "chinook", "schema_version": "v1", **md}
    meta["id"] = f"{kind}:{md.get('table', md.get('datasource_id', 'chinook'))}:{md.get('column', '')}"
    return Document(page_content=text, metadata=meta)


def _store(tmp_path) -> VectorStore:
    store = VectorStore("nl2sql_store", str(tmp_path / "vs"), embeddings=BagEmbeddings())
    store.vectorstore.add_documents([
        _doc("schema.datasource", "music store sales customer invoice"),
        _doc("schema.table", "genre track", table="Genre"),
        _doc("schema.table", "track genre album name", table="Track"),
        _doc("schema.table", "invoice sales customer", table="Invoice"),
        _doc("schema.table", "artist album", table="Artist"),
        _doc("schema.table", "employee", table="Employee"),
        _doc("schema.column", "Genre name. Stats: sample_values Rock, Jazz", table="Genre", column="Name"),
        _doc("schema.column", "track name", table="Track", column="Name"),
        _doc("schema.column", "invoice line price track sales", table="InvoiceLine", column="UnitPrice"),
    ])
    return store


QUESTION = "Which genre sells the most tracks?"


def test_the_record_matches_the_documents_chroma_mmr_returns(tmp_path):
    store = _store(tmp_path)
    where = {"$and": [{"datasource_id": "chinook"}, {"type": {"$in": ["schema.table", "schema.metric"]}}]}
    expected = store.vectorstore.max_marginal_relevance_search(QUESTION, k=2, fetch_k=8, lambda_mult=0.7,
                                                                filter=where)
    explain: list = []

    docs = store.retrieve_schema_context(QUESTION, "chinook", k=2, explain=explain)

    assert [d.metadata["id"] for d in docs] == [d.metadata["id"] for d in expected]
    [record] = explain
    assert record["search"] == "tables"
    assert record["query"] == QUESTION
    assert (record["k"], record["fetch_k"], record["lambda_mult"]) == (2, 8, 0.7)
    assert record["filter"] == {"datasource_id": "chinook", "types": ["schema.table", "schema.metric"]}
    assert sorted(record["picks"]) == sorted(d.metadata["id"] for d in docs)


def test_the_pool_is_every_candidate_nearest_first_with_scores_and_types(tmp_path):
    store = _store(tmp_path)
    explain: list = []

    store.retrieve_schema_context(QUESTION, "chinook", k=2, explain=explain)

    pool = explain[0]["pool"]
    assert len(pool) == 5  # every table entry; fetch_k (8) is more than there are
    assert [e["rank"] for e in pool] == [1, 2, 3, 4, 5]
    sims = [e["similarity"] for e in pool]
    assert sims == sorted(sims, reverse=True)
    assert {e["type"] for e in pool} == {"schema.table"}
    assert pool[0]["label"] in {"Genre", "Track"}
    assert all(e["id"].startswith("schema.table:") for e in pool)
    assert all("text" not in e for e in pool)  # the trace never carries embedded text


def test_picks_are_in_mmr_order_and_the_rest_are_dropped(tmp_path):
    store = _store(tmp_path)
    explain: list = []

    store.retrieve_schema_context(QUESTION, "chinook", k=3, explain=explain)

    record = explain[0]
    by_id = {e["id"]: e for e in record["pool"]}
    first, second, third = (by_id[i] for i in record["picks"])
    # The first pick is simply the most similar entry.
    assert first["rank"] == 1 and first["pick_order"] == 1
    assert first["mmr_score"] == first["similarity"] and first["redundancy"] == 0.0
    # Later picks are scored 0.7 * relevance - 0.3 * similarity to earlier picks.
    for e in (second, third):
        assert math.isclose(e["mmr_score"], 0.7 * e["similarity"] - 0.3 * e["redundancy"], abs_tol=2e-4)
    assert [second["pick_order"], third["pick_order"]] == [2, 3]
    assert set(record["dropped"]) == {e["id"] for e in record["pool"] if not e["picked"]}
    assert len(record["dropped"]) == 2


def test_mmr_skips_a_near_duplicate_for_a_less_similar_entry_that_adds_something(tmp_path):
    store = _store(tmp_path)
    store.vectorstore.add_documents([_doc("schema.table", "genre genre track", table="Playlist")])
    explain: list = []

    store.retrieve_schema_context("genre sales", "chinook", k=2, explain=explain)

    pool = explain[0]["pool"]
    assert [e["label"] for e in pool[:3]] == ["Playlist", "Genre", "Invoice"]
    # Genre is the second most similar, but it nearly repeats Playlist, so
    # Invoice, less similar and different, is picked instead.
    assert explain[0]["picks"] == [pool[0]["id"], pool[2]["id"]]
    assert pool[1]["picked"] is False and pool[1]["id"] in explain[0]["dropped"]
    assert pool[2]["redundancy"] < 0.1


def test_no_explain_list_records_nothing_and_returns_the_same_documents(tmp_path):
    store = _store(tmp_path)
    explain: list = []

    plain = store.retrieve_column_candidates(QUESTION, "chinook", k=2)
    traced = store.retrieve_column_candidates(QUESTION, "chinook", k=2, explain=explain)

    assert [d.metadata["id"] for d in plain] == [d.metadata["id"] for d in traced]
    assert explain[0]["search"] == "columns"
    assert "build_id" not in plain[0].metadata


def test_the_inspector_filters_by_type_and_datasource_and_shows_the_text(tmp_path):
    store = _store(tmp_path)

    everything = store.inspect(QUESTION, k=4)
    columns = store.inspect(QUESTION, k=2, types=["schema.column"], datasource_id="chinook", lambda_mult=1.0)
    elsewhere = store.inspect(QUESTION, k=2, datasource_id="sales")

    assert len(everything["pool"]) == 9
    assert {e["type"] for e in everything["pool"]} == {"schema.datasource", "schema.table", "schema.column"}
    assert {e["type"] for e in columns["pool"]} == {"schema.column"}
    assert columns["filter"] == {"datasource_id": "chinook", "types": ["schema.column"]}
    # lambda 1.0 is pure relevance: the picks are the two nearest entries.
    assert [e["pick_order"] for e in columns["pool"][:2]] == [1, 2]
    assert "Rock, Jazz" in " ".join(e["text"] for e in columns["pool"])
    assert elsewhere["pool"] == [] and elsewhere["picks"] == []


def test_the_inspector_honours_k_and_fetch_k(tmp_path):
    store = _store(tmp_path)

    record = store.inspect(QUESTION, k=1, fetch_k=3)

    assert (record["k"], record["fetch_k"]) == (1, 3)
    assert len(record["pool"]) == 3
    assert len(record["picks"]) == 1


# --- the nodes put the record in the update they return (the trace keeps it) ---

from types import SimpleNamespace  # noqa: E402

from nl2sql.auth import UserContext  # noqa: E402
from nl2sql.pipeline.nodes.datasource_resolver.node import DatasourceResolverNode  # noqa: E402
from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery  # noqa: E402
from nl2sql.pipeline.nodes.schema_retriever.node import SchemaRetrieverNode  # noqa: E402
from nl2sql.pipeline.state import GraphState, SubgraphExecutionState  # noqa: E402
from nl2sql_adapter_sdk.schema import (  # noqa: E402
    ColumnContract,
    SchemaContract,
    SchemaMetadata,
    SchemaSnapshot,
    TableContract,
    TableMetadata,
    TableRef,
)


def _resolver(store, ds_ids=("chinook", "sales")):
    from langchain_openai import ChatOpenAI

    from nl2sql.pipeline.nodes.datasource_resolver.schemas import AnswerabilityResponse

    llm = ChatOpenAI(model="gpt-4o", api_key="sk-fake-never-called")
    node = DatasourceResolverNode(SimpleNamespace(
        vector_store=store,
        rbac=SimpleNamespace(get_allowed_datasources=lambda _ctx: ["*"]),
        ds_registry=SimpleNamespace(list_ids=lambda: list(ds_ids)),
        schema_store=SimpleNamespace(get_latest_version=lambda _id: "v1", get_latest_snapshot=lambda _id: None),
        llm_registry=SimpleNamespace(get_llm=lambda _name: llm),
    ))
    # The answerability check is an LLM call with its own trace entry; here it says yes.
    node._judge_answerability = lambda _q, ids: AnswerabilityResponse(answerable_datasource_ids=list(ids), reason="ok")
    return node


def _two_datasource_store(tmp_path):
    store = _store(tmp_path)
    store.vectorstore.add_documents([
        Document(page_content="hr staff payroll", metadata={"type": "schema.datasource", "datasource_id": "sales",
                                                            "schema_version": "v1", "id": "schema.datasource:sales:v1"}),
    ])
    return store


def test_the_resolver_records_its_search(tmp_path):
    out = _resolver(_two_datasource_store(tmp_path))(
        GraphState(user_query=QUESTION, user_context=UserContext(roles=["admin"])))

    retrieval = out["retrieval"]
    assert retrieval["skipped"] is False
    assert retrieval["query"] == QUESTION
    [search] = retrieval["searches"]
    assert search["search"] == "datasources"
    assert (search["k"], search["fetch_k"], search["lambda_mult"]) == (5, 20, 0.7)
    assert {e["label"] for e in search["pool"]} == {"chinook", "sales"}
    assert all(e["picked"] and isinstance(e["similarity"], float) for e in search["pool"])
    assert "answerab" not in str(retrieval)  # the LLM check is traced as an LLM call, not here


def test_the_resolver_says_when_it_skipped_the_search(tmp_path):
    out = _resolver(_store(tmp_path))(GraphState(user_query=QUESTION, datasource_id="chinook"))

    assert out["retrieval"]["skipped"] is True
    assert "explicit datasource_id" in out["retrieval"]["reason"]


def test_the_resolver_skips_the_search_for_a_single_datasource(tmp_path):
    out = _resolver(_store(tmp_path), ds_ids=("chinook",))(
        GraphState(user_query=QUESTION, user_context=UserContext(roles=["admin"])))

    assert out["retrieval"] == {"skipped": True, "reason": "single datasource: vector search skipped"}


REFS = {name: TableRef(schema_name="main", table_name=name) for name in ("Genre", "Track", "InvoiceLine")}


def _chinook_snapshot() -> SchemaSnapshot:
    return SchemaSnapshot(
        contract=SchemaContract(datasource_id="chinook", engine_type="sqlite", tables={
            ref.full_name: TableContract(table=ref, columns={
                "Name": ColumnContract(name="Name", data_type="text"),
                "UnitPrice": ColumnContract(name="UnitPrice", data_type="real"),
            }, foreign_keys=[])
            for ref in REFS.values()
        }),
        metadata=SchemaMetadata(datasource_id="chinook", engine_type="sqlite", tables={
            ref.full_name: TableMetadata(table=ref, row_count=1, columns={}) for ref in REFS.values()
        }),
    )


def _retriever_store(tmp_path) -> VectorStore:
    store = VectorStore("nl2sql_store", str(tmp_path / "vs"), embeddings=BagEmbeddings())
    g, t, il = (REFS[n].full_name for n in ("Genre", "Track", "InvoiceLine"))
    store.vectorstore.add_documents([
        _doc("schema.table", "genre track", table=g),
        _doc("schema.table", "track genre album name", table=t),
        _doc("schema.table", "invoice line sales price track", table=il),
        _doc("schema.column", "genre name. Stats: Rock, Jazz", table=g, column="Name"),
        _doc("schema.column", "track name", table=t, column="Name"),
        _doc("schema.column", "invoice line price sales", table=il, column="UnitPrice"),
    ])
    return store


def _retriever(tmp_path, allowed_tables):
    return SchemaRetrieverNode(SimpleNamespace(
        vector_store=_retriever_store(tmp_path),
        schema_store=SimpleNamespace(get_latest_snapshot=lambda _id: _chinook_snapshot(),
                                     get_snapshot=lambda _id, _v: _chinook_snapshot()),
        rbac=SimpleNamespace(get_allowed_tables=lambda _ctx: allowed_tables),
    ))


def _sub_query_state():
    return SubgraphExecutionState(
        trace_id="t",
        sub_query=SubQuery(id="sq1", datasource_id="chinook", intent="genre track sales"),
        user_context=UserContext(roles=["analyst"]),
    )


def test_the_schema_retriever_records_its_searches_and_what_survived(tmp_path, monkeypatch):
    monkeypatch.setattr("nl2sql.pipeline.nodes.schema_retriever.node.settings.schema_retrieval_full_snapshot_max_tables", 0)

    out = _retriever(tmp_path, ["*"])(_sub_query_state())

    retrieval = out["retrieval"]
    assert retrieval["skipped"] is False
    assert retrieval["query"] == "genre track sales"
    assert [s["search"] for s in retrieval["searches"]] == ["tables", "planning"]
    planning = retrieval["searches"][1]
    assert planning["filter"]["tables"]  # the tables the first search picked
    assert {t["table"] for t in retrieval["tables"]} == {t.name for t in out["relevant_tables"]}
    assert all(e["similarity"] is not None for s in retrieval["searches"] for e in s["pool"])


def test_the_trace_withholds_column_scores_for_tables_the_role_cannot_read(tmp_path, monkeypatch):
    monkeypatch.setattr("nl2sql.pipeline.nodes.schema_retriever.node.settings.schema_retrieval_full_snapshot_max_tables", 0)

    out = _retriever(tmp_path, ["chinook.Genre"])(_sub_query_state())

    columns = [e for s in out["retrieval"]["searches"] for e in s["pool"] if e["type"] == "schema.column"]
    assert columns
    for entry in columns:
        if entry["table"] == REFS["Genre"].full_name:
            assert entry["similarity"] is not None and "withheld" not in entry
        else:
            # Structure stays: the entry, its rank, whether it was picked.
            assert entry["similarity"] is None and entry["mmr_score"] is None
            assert entry["withheld"] and entry["label"] and entry["rank"]
    # Table entries carry names only, no data, so they keep their scores.
    tables = [e for e in out["retrieval"]["searches"][0]["pool"]]
    assert all(e["similarity"] is not None for e in tables)


def test_the_schema_retriever_says_when_the_schema_is_small_enough_to_skip(tmp_path):
    out = _retriever(tmp_path, ["*"])(_sub_query_state())

    retrieval = out["retrieval"]
    assert retrieval["skipped"] is True
    assert "3 tables" in retrieval["reason"] and "limit" in retrieval["reason"]
    assert "searches" not in retrieval
    assert len(retrieval["tables"]) == 3
