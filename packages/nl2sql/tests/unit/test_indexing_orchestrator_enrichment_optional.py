"""Indexing must survive an unusable enrichment LLM.

Enrichment is a best-effort embellishment of the schema snapshot. These tests
pin the contract that it can never take indexing down with it: no API key, an
unreachable endpoint and a mid-invoke failure all have to leave a real set of
chunks in the vector store.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from index_helpers import TestIndex  # same directory
from langchain_core.embeddings import FakeEmbeddings
from langchain_core.runnables import RunnableLambda

from nl2sql.indexing import enrichment_service
from nl2sql.indexing.orchestrator import IndexingOrchestrator
from nl2sql.llm.models import AgentConfig
from nl2sql.llm.registry import LLMRegistry
from nl2sql.schema import InMemorySchemaStore
from nl2sql.secrets import SecretManager
from nl2sql_sdk.stores import Filter
from nl2sql_sdk.schema import (
    ColumnContract,
    SchemaContract,
    SchemaMetadata,
    SchemaSnapshot,
    TableContract,
    TableMetadata,
    TableRef,
)


def _snapshot() -> SchemaSnapshot:
    table_ref = TableRef(schema_name="public", table_name="users")
    contract = SchemaContract(
        datasource_id="ds1",
        engine_type="sqlite",
        tables={
            table_ref.full_name: TableContract(
                table=table_ref,
                columns={
                    "id": ColumnContract(name="id", data_type="int"),
                    "email": ColumnContract(name="email", data_type="text"),
                },
            )
        },
    )
    metadata = SchemaMetadata(
        datasource_id="ds1",
        engine_type="sqlite",
        tables={table_ref.full_name: TableMetadata(table=table_ref, columns={})},
    )
    return SchemaSnapshot(contract=contract, metadata=metadata)


def _entries(stats) -> int:
    """How many entries a build wrote, from its counts by type."""
    return sum(v for v in stats.values() if isinstance(v, int) and not isinstance(v, bool))


def _indexed(ctx):
    """Every entry in the index, as written."""
    return ctx.index.get(ctx.namespace, Filter())


class _StubAdapter:
    datasource_id = "ds1"

    def fetch_schema_snapshot(self):
        return _snapshot()


def _context(tmp_path, llm_registry) -> SimpleNamespace:
    ctx = TestIndex(tmp_path, FakeEmbeddings(size=8), schema_store=InMemorySchemaStore()).ctx
    ctx.ds_registry = SimpleNamespace(
        get_sample_questions=lambda ds_id: ["How many users are there?"],
        get_description=lambda ds_id: "User records",
    )
    ctx.llm_registry = llm_registry
    ctx.curated = None
    return ctx


@pytest.fixture()
def keyless_registry(monkeypatch) -> LLMRegistry:
    """A registry whose only agent is an OpenAI one with no resolvable key."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    registry = LLMRegistry(SecretManager())
    registry.register_llm(
        AgentConfig(name="indexing_enrichment", provider="openai", model="gpt-4o-mini")
    )
    return registry


def test_indexing_produces_chunks_without_an_api_key(tmp_path, keyless_registry):
    ctx = _context(tmp_path, keyless_registry)

    stats = IndexingOrchestrator(ctx, enrich=True).index_datasource(_StubAdapter())

    assert _entries(stats) > 0
    assert _indexed(ctx), "indexing produced no entries without a key"


def test_missing_key_leaves_the_snapshot_unenriched(keyless_registry, caplog):
    snapshot = _snapshot()

    with caplog.at_level("INFO"):
        result, questions = enrichment_service.enrich_schema_snapshot(
            snapshot=snapshot,
            llm_registry=keyless_registry,
            datasource_description="User records",
            existing_questions=["existing question"],
        )

    assert result == snapshot
    assert questions == ["existing question"]
    assert "enrichment" in caplog.text.lower()


def test_enrichment_failure_mid_invoke_degrades_instead_of_raising(tmp_path, caplog):
    """A reachable LLM that blows up during the call is no different to no LLM."""

    def _boom(_payload):
        raise RuntimeError("upstream 503")

    class _ExplodingLLM:
        def with_structured_output(self, *_args, **_kwargs):
            return RunnableLambda(_boom)

    registry = SimpleNamespace(get_llm=lambda name: _ExplodingLLM())
    ctx = _context(tmp_path, registry)

    with caplog.at_level("WARNING"):
        stats = IndexingOrchestrator(ctx, enrich=True).index_datasource(_StubAdapter())

    assert _entries(stats) > 0
    assert "upstream 503" in caplog.text


def test_unusable_llm_client_degrades_instead_of_raising(tmp_path):
    """``with_structured_output`` failing is caught too, not just ``invoke``."""

    class _BrokenLLM:
        def with_structured_output(self, *_args, **_kwargs):
            raise ValueError("structured output unsupported by this endpoint")

    ctx = _context(tmp_path, SimpleNamespace(get_llm=lambda name: _BrokenLLM()))

    stats = IndexingOrchestrator(ctx, enrich=True).index_datasource(_StubAdapter())

    assert _entries(stats) > 0


def test_enrichment_off_never_asks_for_an_llm(tmp_path):
    """Enrichment is off unless asked for: no LLM is even resolved, so no tokens are spent."""

    def _no_llm(name):
        raise AssertionError(f"enrichment asked for the '{name}' LLM while off")

    ctx = _context(tmp_path, SimpleNamespace(get_llm=_no_llm))

    stats = IndexingOrchestrator(ctx).index_datasource(_StubAdapter())

    assert _entries(stats) > 0
