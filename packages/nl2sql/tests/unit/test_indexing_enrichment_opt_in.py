"""Enrichment is opt-in, keeps the database's own comments, and fails quietly.

Also pins that a datasource added in code (``engine.add_datasource``) keeps
its description at index time, instead of the orchestrator re-reading the
datasources YAML, which does not list it.
"""

from __future__ import annotations

import inspect
import logging
import sqlite3
from types import SimpleNamespace

from index_helpers import TestIndex, memory_state  # same directory
from langchain_core.embeddings import FakeEmbeddings
from langchain_core.runnables import RunnableLambda

from nl2sql.indexing.orchestrator import IndexingOrchestrator
from nl2sql.datasources import DatasourceRegistry
from nl2sql.config import Datasource, Limits
from nl2sql.indexing.enrichment_service import (
    ColumnEnrichment,
    DatasourceEnrichment,
    SchemaEnrichment,
    TableEnrichment,
    apply_enrichment,
)
from nl2sql.indexing.orchestrator import IndexingOrchestrator
from nl2sql.secrets import SecretManager
from nl2sql_sdk.stores import Filter
from nl2sql_sdk import IndexScope
from nl2sql_sdk.schema import (
    ColumnContract,
    ColumnMetadata,
    SchemaContract,
    SchemaMetadata,
    SchemaSnapshot,
    TableContract,
    TableMetadata,
    TableRef,
)


def _snapshot(table_comment=None, column_comment=None) -> SchemaSnapshot:
    ref = TableRef(schema_name="public", table_name="users")
    contract = SchemaContract(
        datasource_id="ds1",
        engine_type="sqlite",
        tables={
            ref.full_name: TableContract(
                table=ref,
                columns={
                    "id": ColumnContract(name="id", data_type="int"),
                    "email": ColumnContract(name="email", data_type="text"),
                },
            )
        },
    )
    columns = {"email": ColumnMetadata(description=column_comment)} if column_comment else {}
    metadata = SchemaMetadata(
        datasource_id="ds1",
        engine_type="sqlite",
        tables={ref.full_name: TableMetadata(table=ref, description=table_comment, columns=columns)},
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

    def fetch_schema_snapshot(self, scope, on_table=None):
        return _snapshot()


class _RecordingLLMRegistry:
    """Counts every request for an LLM; the LLM answers with an empty enrichment."""

    def __init__(self):
        self.calls = 0

    def get_llm(self, name):
        self.calls += 1

        class _LLM:
            def with_structured_output(self, *_a, **_kw):
                return RunnableLambda(
                    lambda _p: SchemaEnrichment(datasource=DatasourceEnrichment())
                )

        return _LLM()


def _context(tmp_path, llm_registry, ds_registry=None, file_description="User records"):
    ds_registry = ds_registry or SimpleNamespace(
        get_adapter=lambda ds_id: _StubAdapter(),
        list_ids=lambda: ["ds1"],
        get_description=lambda ds_id: file_description,
        get_index_scope=lambda _ds: IndexScope(), get_sample_questions=lambda ds_id: [],
    )
    ctx = TestIndex(tmp_path, FakeEmbeddings(size=8), state=memory_state()).ctx
    ctx.llm_registry = llm_registry
    ctx.ds_registry = ds_registry
    ctx.curated = None
    return ctx


# --- B8: enrichment is opt-in -------------------------------------------------

def test_orchestrator_enriches_when_asked(tmp_path):
    registry = _RecordingLLMRegistry()
    IndexingOrchestrator(_context(tmp_path, registry), enrich=True).index_datasource(_StubAdapter())

    assert registry.calls == 1


def test_orchestrator_does_not_enrich_by_default(tmp_path):
    registry = _RecordingLLMRegistry()

    IndexingOrchestrator(_context(tmp_path, registry)).index_datasource(_StubAdapter())

    assert registry.calls == 0


def test_every_public_entry_point_defaults_to_no_enrichment():
    from nl2sql.api.indexing_api import IndexAPI
    from nl2sql.cli.common.indexing import run_indexing
    from nl2sql.indexing.rebuild import rebuild_index

    for fn in (IndexAPI.build, rebuild_index, inspect.unwrap(run_indexing)):
        assert inspect.signature(fn).parameters["enrich"].default is False, fn.__qualname__


def test_enrichment_failure_is_one_warning_line_without_traceback(tmp_path, caplog):
    def _boom(_payload):
        raise RuntimeError("Error code: 401 - invalid api key")

    class _LLM:
        def with_structured_output(self, *_a, **_kw):
            return RunnableLambda(_boom)

    ctx = _context(tmp_path, SimpleNamespace(get_llm=lambda name: _LLM()))

    with caplog.at_level(logging.WARNING):
        stats = IndexingOrchestrator(ctx, enrich=True).index_datasource(_StubAdapter())

    assert _entries(stats) > 0
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "401" in warnings[0].getMessage()
    assert warnings[0].exc_info is None
    assert "\n" not in warnings[0].getMessage()


# --- B8: database comments survive enrichment ---------------------------------

def test_enrichment_never_overwrites_a_database_comment():
    snapshot = _snapshot(table_comment="Registered users (from DB)", column_comment="Login email")
    table_key = next(iter(snapshot.contract.tables))
    enrichment = SchemaEnrichment(
        datasource=DatasourceEnrichment(description="LLM datasource text"),
        tables={table_key: TableEnrichment(description="LLM table text")},
        columns={
            f"{table_key}.email": ColumnEnrichment(description="LLM email text"),
            f"{table_key}.id": ColumnEnrichment(description="LLM id text"),
        },
    )

    result = apply_enrichment(snapshot, enrichment)

    table_md = result.metadata.tables[table_key]
    assert table_md.description == "Registered users (from DB)"
    assert table_md.columns["email"].description == "Login email"
    # Blanks are still filled.
    assert table_md.columns["id"].description == "LLM id text"


# --- B9: a code-added datasource keeps its description ------------------------

def test_code_added_datasource_description_reaches_the_datasource_chunk(tmp_path):
    database = tmp_path / "shop.sqlite"
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, total REAL)")

    ds_registry = DatasourceRegistry(SecretManager())
    ds_registry.register_datasource(Datasource(
        id="shop",
        description="Online shop orders and revenue",
        type="sqlite", connection={"database": str(database)},
    ))
    # The datasources YAML on disk does not list "shop".
    ctx = _context(tmp_path, _RecordingLLMRegistry(), ds_registry=ds_registry, file_description=None)

    IndexingOrchestrator(ctx).index_datasource(ds_registry.get_adapter("shop"))

    [datasource_entry] = [e for e in _indexed(ctx) if e.metadata["type"] == "schema.datasource"]
    assert "Online shop orders and revenue" in datasource_entry.text
