"""The retrieval record against the real local embedder (all-MiniLM-L6-v2, ONNX).

A small Chinook-like index built in tmp: the record's picks must be exactly the
documents ``Chroma.max_marginal_relevance_search`` returns, and the scores must
be the cosine similarities of the real vectors.
"""
from __future__ import annotations

import pytest
from langchain_core.documents import Document

from nl2sql.indexing.embeddings import LocalEmbeddings
from nl2sql.indexing.vector_store import VectorStore

pytestmark = pytest.mark.integration

TABLES = {
    "Genre": "Table: Genre\nMusic genres such as Rock and Jazz.\nPrimary Key: GenreId\nColumns: GenreId, Name",
    "Track": "Table: Track\nSongs with their album, genre, media type and unit price.\nColumns: TrackId, Name, AlbumId, GenreId, UnitPrice",
    "InvoiceLine": "Table: InvoiceLine\nOne purchased track on an invoice, with quantity and price.\nColumns: InvoiceLineId, InvoiceId, TrackId, UnitPrice, Quantity",
    "Invoice": "Table: Invoice\nA customer's purchase: date, billing address and total.\nColumns: InvoiceId, CustomerId, InvoiceDate, Total",
    "Employee": "Table: Employee\nStaff with titles and managers.\nColumns: EmployeeId, LastName, FirstName, Title, ReportsTo",
    "MediaType": "Table: MediaType\nFile formats such as MPEG audio.\nColumns: MediaTypeId, Name",
}


@pytest.fixture(scope="module")
def store(tmp_path_factory):
    vs = VectorStore("nl2sql_store", str(tmp_path_factory.mktemp("vs")), embeddings=LocalEmbeddings())
    vs.vectorstore.add_documents([
        Document(page_content=text, metadata={"id": f"schema.table:{name}:v1", "type": "schema.table",
                                              "datasource_id": "chinook", "table": name, "schema_version": "v1"})
        for name, text in TABLES.items()
    ])
    return vs


QUESTION = "Which genre sells the most tracks?"


def test_the_record_is_the_search_chroma_runs(store):
    where = {"$and": [{"datasource_id": "chinook"}, {"type": {"$in": ["schema.table", "schema.metric"]}}]}
    expected = store.vectorstore.max_marginal_relevance_search(QUESTION, k=3, fetch_k=12, lambda_mult=0.7,
                                                                filter=where)
    explain: list = []

    docs = store.retrieve_schema_context(QUESTION, "chinook", k=3, explain=explain)

    assert [d.metadata["id"] for d in docs] == [d.metadata["id"] for d in expected]
    record = explain[0]
    assert sorted(record["picks"]) == sorted(d.metadata["id"] for d in docs)
    assert len(record["pool"]) == len(TABLES)
    sims = [e["similarity"] for e in record["pool"]]
    assert sims == sorted(sims, reverse=True) and all(-1 <= s <= 1 for s in sims)
    # Chroma's distance is squared L2; the vectors are unit length, so d = 2 - 2cos.
    for e in record["pool"]:
        assert abs(e["distance"] - (2 - 2 * e["similarity"])) < 1e-3
    assert {"Genre", "Track"} & {e["label"] for e in record["pool"][:2]}


def test_lambda_one_is_plain_nearest_neighbour_order(store):
    record = store.inspect(QUESTION, k=4, lambda_mult=1.0)

    assert [e["pick_order"] for e in record["pool"][:4]] == [1, 2, 3, 4]
    assert all(e["text"] for e in record["pool"])
