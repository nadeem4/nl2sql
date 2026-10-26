"""Knowledge in the index, ``refresh``, and the snapshot that holds only the database's words (wave_b.md, section 4; K3).

The index carries knowledge for ranking (D56): the chosen descriptions in table
and column entries, ``knowledge.term`` entries, glossary joins as relationship
entries, and the glossary's ``examples`` as example entries. The build pointer
records the datasource's ``knowledge_hash``; a changed glossary makes health
``stale``. ``refresh`` renders every entry's text again from the live build's
stored snapshot, the curated section and the knowledge, reads no schema, calls
no model, and embeds only the entries whose text changed. Nothing a model wrote
reaches a snapshot (B31).
"""
from __future__ import annotations

import inspect
import math
import re
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import List

import pytest
from index_helpers import TestIndex, memory_state  # same directory
from langchain_core.embeddings import Embeddings

from nl2sql.config import Curated
from nl2sql.indexing.health import index_health
from nl2sql.indexing.orchestrator import IndexingOrchestrator
from nl2sql.indexing.rebuild import rebuild_index
from nl2sql.knowledge.merge import Knowledge, LoadedSource, merge
from nl2sql_sdk import IndexScope
from nl2sql_sdk.knowledge import Glossary
from nl2sql_sdk.schema import (
    ColumnContract,
    ColumnMetadata,
    ForeignKeyContract,
    SchemaContract,
    SchemaMetadata,
    SchemaSnapshot,
    TableContract,
    TableMetadata,
    TableRef,
)
from nl2sql_sdk.stores import Filter

VOCAB = ["revenue", "sales", "turnover", "customer", "order", "invoice", "total", "country", "money", "region",
         "many", "orders"]


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


def _table(name, columns, comment=None, column_comments=None, foreign_keys=()):
    ref = TableRef(schema_name="main", table_name=name)
    contract = TableContract(table=ref, columns={
        column: ColumnContract(name=column, data_type=spec.split()[0], is_primary_key=spec.endswith(" pk"))
        for column, spec in columns.items()}, foreign_keys=list(foreign_keys))
    metadata = TableMetadata(table=ref, description=comment, columns={
        column: ColumnMetadata(description=text) for column, text in (column_comments or {}).items()})
    return ref.full_name, contract, metadata


def _snapshot() -> SchemaSnapshot:
    refs = TableRef(schema_name="main", table_name="Customer")
    tables = [
        _table("Customer", {"CustomerId": "INTEGER pk", "Country": "TEXT"}, comment="Customer master (database).",
               column_comments={"Country": "Country code (database)."}),
        _table("Invoice", {"InvoiceId": "INTEGER pk", "CustomerId": "INTEGER", "Total": "REAL"},
               comment="Invoices (database).",
               foreign_keys=[ForeignKeyContract(constrained_columns=["CustomerId"], referred_table=refs,
                                                referred_columns=["CustomerId"])]),
        _table("Region", {"RegionId": "INTEGER pk", "CustomerId": "INTEGER"}),
    ]
    return SchemaSnapshot(
        contract=SchemaContract(datasource_id="shop", engine_type="sqlite", tables={k: c for k, c, _ in tables}),
        metadata=SchemaMetadata(datasource_id="shop", engine_type="sqlite", tables={k: m for k, _, m in tables}))


GLOSSARY = {
    "version": 1,
    "datasources": {"shop": {
        "tables": {"Customer": {"description": "People who buy things (glossary)."}},
        "columns": {"Invoice.Total": {"description": "Invoice amount in dollars.", "synonyms": ["amount due"],
                                      "role": "money"}},
        "joins": [{"from": "Region.CustomerId", "to": "Customer.CustomerId", "cardinality": "many-to-one"}],
        "terms": [{"term": "revenue", "synonyms": ["sales"], "definition": "Sum of Invoice.Total.",
                   "columns": ["Invoice.Total"]}],
        "examples": ["Which country has the most customers?"],
    }},
}


def knowledge_of(data=GLOSSARY) -> Knowledge:
    return merge([LoadedSource(type="yaml", glossary=Glossary.model_validate(data), sha256=None, path=None,
                               loaded_at=datetime.now(timezone.utc))])


class _Adapter:
    datasource_id = "shop"

    def __init__(self):
        self.reads = 0

    def fetch_schema_snapshot(self, scope, on_table=None):
        self.reads += 1
        return _snapshot()


class _NoLLM:
    # The secrets the redactor reads (an event's error text is redacted) are not a use of a model.
    secret_manager = llms = None

    def __getattr__(self, name):
        raise AssertionError(f"the LLM registry was used ({name})")


def _ctx(tmp_path, knowledge=None, curated=None, embeddings=None):
    adapter = _Adapter()
    store = TestIndex(tmp_path, embeddings or _Words(), state=memory_state())
    ctx = store.ctx
    ctx.llm_registry = _NoLLM()
    ctx.ds_registry = SimpleNamespace(list_adapters=lambda: [adapter], get_index_scope=lambda _ds: IndexScope(),
                                      get_sample_questions=lambda _ds: ["How many invoices are there?"],
                                      get_description=lambda _ds: "An online shop", get_dialect=lambda _ds: "sqlite")
    ctx.knowledge = knowledge if knowledge is not None else knowledge_of()
    ctx.curated = curated
    ctx.store = store
    return ctx, adapter


def _pointer(ctx):
    return ctx.index.pointers(ctx.namespace)["shop"]


def _live(ctx, kind=None):
    where = Filter(eq={"build_id": _pointer(ctx).build_id, **({"type": kind} if kind else {})})
    return ctx.index.get(ctx.namespace, where, with_vectors=True)


def _text(ctx, kind, **match):
    [entry] = [e for e in _live(ctx, kind) if all(e.metadata.get(k) == v for k, v in match.items())]
    return entry.text


def _built(tmp_path, **kwargs):
    ctx, adapter = _ctx(tmp_path, **kwargs)
    assert rebuild_index(ctx).ok
    return ctx, adapter


def _curated(*questions) -> Curated:
    return Curated.model_validate({"datasources": {"shop": {"verified_queries": [{
        "id": "top_customers", "description": "Customers by revenue",
        "questions": list(questions), "sql": "SELECT 1"}]}}})


# --------------------------------------------------------------------- the entries


def test_glossary_description_is_in_the_table_entry(tmp_path):
    ctx, _ = _built(tmp_path)

    customer = _text(ctx, "schema.table", table="[main].[Customer]")
    # The glossary's words win; a table the glossary does not describe keeps the database's comment.
    assert "People who buy things (glossary)." in customer
    assert "Customer master (database)." not in customer
    assert "Invoices (database)." in _text(ctx, "schema.table", table="[main].[Invoice]")


def test_glossary_column_facts_are_in_the_column_entry(tmp_path):
    ctx, _ = _built(tmp_path)

    total = _text(ctx, "schema.column", table="[main].[Invoice]", column="Total")
    assert "Invoice amount in dollars." in total
    assert "amount due" in total
    assert "Role: money" in total
    # A column the glossary leaves alone keeps the database's comment, and gains no role line.
    country = _text(ctx, "schema.column", table="[main].[Customer]", column="Country")
    assert "Country code (database)." in country and "Role:" not in country


def test_terms_are_indexed_and_matched_by_similarity(tmp_path):
    ctx, _ = _built(tmp_path)

    text = _text(ctx, "knowledge.term", term="revenue")
    assert "revenue" in text and "sales" in text and "Sum of Invoice.Total." in text

    retriever = ctx.store.retriever()
    found = retriever.retrieve_term_candidates("total sales", "shop", k=8)
    assert [(c.term, c.datasource_id) for c in found] == [("revenue", "shop")]
    assert found[0].score >= 0.6
    # Another datasource's terms are not candidates, and nothing is embedded when there are none.
    assert retriever.retrieve_term_candidates("sales", "music", k=8) == []


def test_term_entries_are_one_per_term_and_none_without_terms(tmp_path):
    ctx, _ = _built(tmp_path, knowledge=Knowledge())

    assert _live(ctx, "knowledge.term") == []
    # A term is found without an embedding call when the datasource has none.
    retriever = ctx.store.retriever()
    retriever.embeddings.embed_query = lambda text: (_ for _ in ()).throw(AssertionError("embedded a query"))
    assert retriever.retrieve_term_candidates("sales", "shop", k=8) == []


def test_glossary_join_is_a_relationship_entry(tmp_path):
    ctx, _ = _built(tmp_path)

    by_origin = {e.metadata["origin"]: e for e in _live(ctx, "schema.relationship")}
    assert set(by_origin) == {"database", "glossary"}
    joined = by_origin["glossary"]
    assert (joined.metadata["from_table"], joined.metadata["to_table"]) == ("[main].[Region]", "[main].[Customer]")
    assert joined.metadata["from_columns"] == "CustomerId" and joined.metadata["to_columns"] == "CustomerId"
    assert joined.metadata["cardinality"] == "many-to-one"
    assert "many-to-one" in joined.text
    assert by_origin["database"].metadata["from_table"] == "[main].[Invoice]"


def test_a_glossary_join_that_does_not_resolve_is_not_indexed(tmp_path):
    data = {"version": 1, "datasources": {"shop": {"joins": [
        {"from": "Region.CustomerId", "to": "Nowhere.Id"}]}}}
    ctx, _ = _built(tmp_path, knowledge=knowledge_of(data))

    assert [e.metadata["origin"] for e in _live(ctx, "schema.relationship")] == ["database"]


def test_glossary_examples_are_example_entries(tmp_path):
    ctx, _ = _built(tmp_path)

    assert sorted(e.text for e in _live(ctx, "schema.datasource_example")) == [
        "How many invoices are there?", "Which country has the most customers?"]


def test_without_knowledge_the_entries_are_the_databases(tmp_path):
    ctx, _ = _built(tmp_path, knowledge=Knowledge())

    assert "Customer master (database)." in _text(ctx, "schema.table", table="[main].[Customer]")
    assert "Role:" not in _text(ctx, "schema.column", table="[main].[Invoice]", column="Total")
    assert [e.metadata["origin"] for e in _live(ctx, "schema.relationship")] == ["database"]


# ------------------------------------------------------------------ the pointer and health


def test_the_pointer_records_the_knowledge_hash(tmp_path):
    ctx, _ = _built(tmp_path)

    assert _pointer(ctx).knowledge_hash == ctx.knowledge.digest("shop")


def _health(ctx, digests):
    return index_health(ctx.index, ctx.namespace, ctx.state, ["shop"], knowledge_digests=digests)


def test_changed_glossary_makes_health_stale(tmp_path):
    ctx, _ = _built(tmp_path)

    assert _health(ctx, {"shop": ctx.knowledge.digest("shop")}).ok

    edited = knowledge_of({"version": 1, "datasources": {"shop": {"terms": [
        {"term": "revenue", "definition": "A new definition."}]}}})
    health = _health(ctx, {"shop": edited.digest("shop")})
    assert health.status == "stale"
    assert any("glossary changed" in p and "nl2sql index --refresh --datasource shop" in p for p in health.problems)


def test_a_build_made_without_knowledge_is_stale_once_a_glossary_exists(tmp_path):
    ctx, _ = _built(tmp_path, knowledge=Knowledge())

    assert _health(ctx, {"shop": Knowledge().digest("shop")}).ok
    assert _health(ctx, {"shop": knowledge_of().digest("shop")}).status == "stale"


def test_without_digests_health_compares_nothing(tmp_path):
    ctx, _ = _built(tmp_path)

    assert _health(ctx, None).ok


# ------------------------------------------------------------------------------ refresh


def test_refresh_reads_no_schema_and_calls_no_model(tmp_path):
    ctx, adapter = _built(tmp_path)
    reads, old = adapter.reads, _pointer(ctx)
    ctx.knowledge = knowledge_of({"version": 1, "datasources": {"shop": {"terms": [
        {"term": "revenue", "definition": "A new definition."}]}}})

    result = rebuild_index(ctx, refresh=True)

    assert result.ok, result.errors
    assert adapter.reads == reads
    new = _pointer(ctx)
    assert new.build_id != old.build_id and new.schema_version == old.schema_version
    assert new.knowledge_hash == ctx.knowledge.digest("shop") != old.knowledge_hash
    assert "A new definition." in _text(ctx, "knowledge.term", term="revenue")
    # The glossary no longer describes the table, so the database's comment is the chosen one again.
    assert "Customer master (database)." in _text(ctx, "schema.table", table="[main].[Customer]")
    assert _health(ctx, {"shop": ctx.knowledge.digest("shop")}).ok


def test_refresh_embeds_only_changed_entries(tmp_path):
    embeddings = _Words()
    ctx, _ = _built(tmp_path, embeddings=embeddings)
    before = {e.metadata["id"]: tuple(e.vector) for e in _live(ctx)}
    texts = []
    original = embeddings.embed_documents

    def spy(batch):
        texts.extend(batch)
        return original(batch)

    embeddings.embed_documents = spy
    data = {**GLOSSARY, "datasources": {"shop": {**GLOSSARY["datasources"]["shop"], "terms": [
        {"term": "revenue", "synonyms": ["sales"], "definition": "Sum of Invoice.Total, less refunds.",
         "columns": ["Invoice.Total"]}]}}}
    ctx.knowledge = knowledge_of(data)

    assert rebuild_index(ctx, refresh=True).ok

    assert len(texts) == 1 and "less refunds" in texts[0]
    after = {e.metadata["id"]: tuple(e.vector) for e in _live(ctx)}
    assert set(after) == set(before)
    unchanged = [i for i in before if "knowledge.term" not in i]
    assert all(after[i] == before[i] for i in unchanged)


def test_refresh_drops_an_entry_that_is_no_longer_written(tmp_path):
    ctx, _ = _built(tmp_path)
    ctx.knowledge = Knowledge()

    assert rebuild_index(ctx, refresh=True).ok
    assert _live(ctx, "knowledge.term") == []


def test_refresh_swaps_the_curated_entries(tmp_path):
    ctx, _ = _built(tmp_path, curated=_curated("Top customers"))
    ctx.curated = _curated("Top customers", "Best customers")

    result = rebuild_index(ctx, refresh=True)

    assert result.ok
    assert sorted(e.text for e in _live(ctx, "curated.verified_query")) == [
        "Best customers", "Customers by revenue", "Top customers"]
    assert result.stats[0]["curated.verified_query"] == 3 and result.stats[0]["knowledge.term"] == 1


def test_refresh_of_an_unindexed_datasource_fails_for_that_datasource(tmp_path):
    ctx, _ = _ctx(tmp_path)

    result = rebuild_index(ctx, refresh=True)

    assert not result.ok
    assert "nl2sql index" in result.errors[0]["error"]


def test_the_orchestrator_refresh_is_not_called_refresh_curated():
    assert hasattr(IndexingOrchestrator, "refresh")
    assert not hasattr(IndexingOrchestrator, "refresh_curated")


# ------------------------------------------------------------------ B31, and the removed options


def test_snapshot_holds_only_database_text(tmp_path):
    ctx, _ = _ctx(tmp_path)  # a glossary describing Customer, and an LLM registry that fails if it is touched

    IndexingOrchestrator(ctx).index_datasource(_Adapter())

    stored = ctx.state.get_snapshot("shop", _pointer_version(ctx))
    table = stored.metadata.tables["[main].[Customer]"]
    assert table.description == "Customer master (database)."
    assert table.columns["Country"].description == "Country code (database)."
    assert stored.metadata.tables["[main].[Region]"].description is None


def _pointer_version(ctx) -> str:
    return ctx.index.pointers(ctx.namespace)["shop"].schema_version


def test_enrich_option_is_gone():
    from nl2sql.api.indexing_api import IndexAPI
    from nl2sql.cli.common.indexing import run_indexing

    for fn in (IndexAPI.build, rebuild_index, inspect.unwrap(run_indexing)):
        parameters = inspect.signature(fn).parameters
        assert "enrich" not in parameters and "curated_only" not in parameters, fn.__qualname__
    assert "enrich" not in inspect.signature(IndexingOrchestrator.__init__).parameters
    with pytest.raises(ImportError):
        __import__("nl2sql.indexing.enrichment_service")


def test_the_cli_has_refresh_and_not_enrich_or_curated_only():
    from typer.testing import CliRunner

    from nl2sql.cli import main

    result = CliRunner().invoke(main.app, ["index", "--help"])
    assert "--refresh" in result.output
    assert "--enrich" not in result.output and "--curated-only" not in result.output
    for option in ("--enrich", "--curated-only"):
        assert CliRunner().invoke(main.app, ["index", option]).exit_code == 2


def test_index_refresh_is_on_the_facade():
    from nl2sql.api.indexing_api import ENTRY_TYPES, IndexAPI

    assert "knowledge.term" in ENTRY_TYPES
    assert list(inspect.signature(IndexAPI.refresh).parameters) == [
        "self", "datasource_ids", "on_progress", "on_datasource"]
