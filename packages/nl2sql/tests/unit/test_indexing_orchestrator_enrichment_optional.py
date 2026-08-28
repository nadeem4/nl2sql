"""Indexing must survive an unusable enrichment LLM.

Enrichment is a best-effort embellishment of the schema snapshot. These tests
pin the contract that it can never take indexing down with it: no API key, an
unreachable endpoint and a mid-invoke failure all have to leave a real set of
chunks in the vector store.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain_core.runnables import RunnableLambda

from nl2sql.indexing import enrichment_service
from nl2sql.indexing.orchestrator import IndexingOrchestrator
from nl2sql.llm.models import AgentConfig
from nl2sql.llm.registry import LLMRegistry
from nl2sql.schema import InMemorySchemaStore
from nl2sql.secrets import SecretManager
from nl2sql_adapter_sdk.schema import (
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


class _RecordingVectorStore:
    def __init__(self):
        self.chunks = []

    def clear(self):
        self.chunks = []

    def refresh_schema_chunks(self, datasource_id, schema_version, chunks, evicted_versions):
        self.chunks = list(chunks)
        return {"datasource_id": datasource_id, "schema_version": schema_version, "chunks": len(chunks)}


class _StubAdapter:
    datasource_id = "ds1"

    def fetch_schema_snapshot(self):
        return _snapshot()


def _context(llm_registry) -> SimpleNamespace:
    return SimpleNamespace(
        vector_store=_RecordingVectorStore(),
        schema_store=InMemorySchemaStore(),
        config_manager=SimpleNamespace(
            get_example_questions=lambda ds_id: ["How many users are there?"],
            get_datasource_description=lambda ds_id: "User records",
        ),
        llm_registry=llm_registry,
    )


@pytest.fixture()
def keyless_registry(monkeypatch) -> LLMRegistry:
    """A registry whose only agent is an OpenAI one with no resolvable key."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    registry = LLMRegistry(SecretManager())
    registry.register_llm(
        AgentConfig(name="indexing_enrichment", provider="openai", model="gpt-4o-mini")
    )
    return registry


def test_indexing_produces_chunks_without_an_api_key(keyless_registry):
    ctx = _context(keyless_registry)

    stats = IndexingOrchestrator(ctx).index_datasource(_StubAdapter())

    assert stats["chunks"] > 0
    assert ctx.vector_store.chunks, "indexing produced no chunks without a key"


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


def test_enrichment_failure_mid_invoke_degrades_instead_of_raising(caplog):
    """A reachable LLM that blows up during the call is no different to no LLM."""

    def _boom(_payload):
        raise RuntimeError("upstream 503")

    class _ExplodingLLM:
        def with_structured_output(self, *_args, **_kwargs):
            return RunnableLambda(_boom)

    registry = SimpleNamespace(get_llm=lambda name: _ExplodingLLM())
    ctx = _context(registry)

    with caplog.at_level("WARNING"):
        stats = IndexingOrchestrator(ctx).index_datasource(_StubAdapter())

    assert stats["chunks"] > 0
    assert "upstream 503" in caplog.text


def test_unusable_llm_client_degrades_instead_of_raising():
    """``with_structured_output`` failing is caught too, not just ``invoke``."""

    class _BrokenLLM:
        def with_structured_output(self, *_args, **_kwargs):
            raise ValueError("structured output unsupported by this endpoint")

    ctx = _context(SimpleNamespace(get_llm=lambda name: _BrokenLLM()))

    stats = IndexingOrchestrator(ctx).index_datasource(_StubAdapter())

    assert stats["chunks"] > 0
