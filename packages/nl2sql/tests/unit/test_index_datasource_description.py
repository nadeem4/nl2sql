"""A datasource added in code (``engine.add_datasource``) keeps its description at index time.

The orchestrator must not re-read the datasources YAML, which does not list it.
"""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

from index_helpers import TestIndex, memory_state  # same directory
from langchain_core.embeddings import FakeEmbeddings

from nl2sql.config import Datasource
from nl2sql.datasources import DatasourceRegistry
from nl2sql.indexing.orchestrator import IndexingOrchestrator
from nl2sql.secrets import SecretManager
from nl2sql_sdk import IndexScope
from nl2sql_sdk.stores import Filter


def _indexed(ctx):
    """Every entry in the index, as written."""
    return ctx.index.get(ctx.namespace, Filter())


def _context(tmp_path, ds_registry, file_description=None):
    ctx = TestIndex(tmp_path, FakeEmbeddings(size=8), state=memory_state()).ctx
    ctx.ds_registry = ds_registry
    ctx.curated = None
    return ctx


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
    ctx = _context(tmp_path, ds_registry)

    IndexingOrchestrator(ctx).index_datasource(ds_registry.get_adapter("shop"))

    [datasource_entry] = [e for e in _indexed(ctx) if e.metadata["type"] == "schema.datasource"]
    assert "Online shop orders and revenue" in datasource_entry.text
