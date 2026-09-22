"""A context built with an explicit datasource config indexes that config's description.

``NL2SQLContext`` loaded the datasources from ``ds_config_path`` but built its
``ConfigManager`` from the settings paths, and indexing asks that manager for
the description. With an explicit path (integration fixtures, SDK users) the
description came back empty, so the resolver's answerability check saw only
table names.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import yaml

from nl2sql.common.settings import settings
from nl2sql.context import NL2SQLContext
from nl2sql.indexing.orchestrator import IndexingOrchestrator

REPO_ROOT = Path(__file__).resolve().parents[4]
DESCRIPTION = "Tiny bookshop: books and their authors"


class _RecordingStore:
    def refresh_schema_chunks(self, datasource_id, schema_version, chunks, evicted_versions, **_kw):
        return {"datasource_id": datasource_id, "schema_version": schema_version}


def _context(monkeypatch, tmp_path: Path) -> NL2SQLContext:
    project = tmp_path / "elsewhere"
    project.mkdir()
    database = tmp_path / "books.sqlite"
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE Book (BookId INTEGER PRIMARY KEY, Title TEXT)")
    ds_config = tmp_path / "datasources.custom.yaml"
    ds_config.write_text(yaml.safe_dump({"version": 1, "datasources": [{
        "id": "books",
        "description": DESCRIPTION,
        "connection": {"type": "sqlite", "database": str(database)},
    }]}), encoding="utf-8")
    secrets = tmp_path / "secrets.yaml"
    secrets.write_text("version: 1\nproviders: []\n", encoding="utf-8")

    # The settings paths resolve against the working directory, which holds no
    # configs: only the explicit path can supply the description.
    monkeypatch.chdir(project)
    monkeypatch.setattr(settings, "schema_store_path", str(tmp_path / "schema_store.db"))
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    return NL2SQLContext(
        ds_config_path=ds_config,
        secrets_config_path=secrets,
        llm_config_path=REPO_ROOT / "configs" / "llm.demo.yaml",
        policies_config_path=REPO_ROOT / "configs" / "policies.demo.json",
        vector_store_path=tmp_path / "vs",
    )


def test_indexing_stores_the_description_from_the_explicit_datasource_config(monkeypatch, tmp_path):
    ctx = _context(monkeypatch, tmp_path)
    [adapter] = ctx.ds_registry.list_adapters()

    IndexingOrchestrator(ctx, enrich=False).index_datasource(adapter, vector_store=_RecordingStore())

    assert ctx.schema_store.get_latest_snapshot("books").metadata.description == DESCRIPTION
