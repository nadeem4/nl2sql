"""The retrieval record against the real local embedder (all-MiniLM-L6-v2, ONNX).

A small Chinook-like index built in tmp through the ``chroma`` index store: the
record's picks must be exactly the documents returned, and the scores must be
the cosine similarities of the real vectors.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from nl2sql.embeddings import EmbeddingInfo
from nl2sql.embeddings.local import LocalEmbeddings
from nl2sql.indexing.orchestrator import IndexingOrchestrator
from nl2sql.indexing.retrieval import Retriever, namespace_for
from nl2sql.storage.chroma import ChromaIndex
from nl2sql.events import EventBus

pytestmark = pytest.mark.integration

TABLES = {
    "Genre": "Table: Genre\nMusic genres such as Rock and Jazz.\nPrimary Key: GenreId\nColumns: GenreId, Name",
    "Track": "Table: Track\nSongs with their album, genre, media type and unit price.\nColumns: TrackId, Name, AlbumId, GenreId, UnitPrice",
    "InvoiceLine": "Table: InvoiceLine\nOne purchased track on an invoice, with quantity and price.\nColumns: InvoiceLineId, InvoiceId, TrackId, UnitPrice, Quantity",
    "Invoice": "Table: Invoice\nA customer's purchase: date, billing address and total.\nColumns: InvoiceId, CustomerId, InvoiceDate, Total",
    "Employee": "Table: Employee\nStaff with titles and managers.\nColumns: EmployeeId, LastName, FirstName, Title, ReportsTo",
    "MediaType": "Table: MediaType\nFile formats such as MPEG audio.\nColumns: MediaTypeId, Name",
}


class _Chunk:
    type = "schema.table"

    def __init__(self, name, text):
        self.name, self.text = name, text

    def get_page_content(self):
        return self.text

    def get_metadata(self):
        return {"id": f"schema.table:{self.name}:v1", "type": "schema.table", "datasource_id": "chinook",
                "table": self.name, "schema_version": "v1"}


@pytest.fixture(scope="module")
def store(tmp_path_factory):
    embeddings = LocalEmbeddings()
    info = EmbeddingInfo("local", "all-MiniLM-L6-v2", 384)
    index = ChromaIndex(tmp_path_factory.mktemp("index"))
    ctx = SimpleNamespace(index=index, namespace=namespace_for(info), embedding_info=info, embedder=embeddings,
                          state=None, ds_registry=None, llm_registry=None, curated=None,
                          settings=SimpleNamespace(schema_=SimpleNamespace(max_versions=3)), bus=EventBus(),
                          tenant_id="default")
    IndexingOrchestrator(ctx)._commit("chinook", "v1", [_Chunk(n, t) for n, t in TABLES.items()], [], None)
    return Retriever(index, ctx.namespace, embeddings, index.pointers(ctx.namespace))


QUESTION = "Which genre sells the most tracks?"


def test_the_record_is_the_search_that_ran(store):
    explain: list = []

    docs = store.retrieve_schema_context(QUESTION, "chinook", k=3, explain=explain)

    record = explain[0]
    assert [d.metadata["id"] for d in docs] == [e["id"] for e in record["pool"] if e["picked"]]
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
