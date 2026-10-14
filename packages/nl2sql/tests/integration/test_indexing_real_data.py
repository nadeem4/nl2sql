from __future__ import annotations

import copy
import sqlite3
import uuid
from pathlib import Path

import numpy as np
import pytest
import yaml

from nl2sql.runtime import Runtime
from nl2sql.testing.config import make_demo_config
from nl2sql.indexing.orchestrator import IndexingOrchestrator
from nl2sql_sdk.stores import Filter


def _cosine_similarity(left: np.ndarray, right: np.ndarray) -> float:
    left_norm = np.linalg.norm(left)
    right_norm = np.linalg.norm(right)
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return float(np.dot(left, right) / (left_norm * right_norm))


def _project_root() -> Path:
    return Path(__file__).resolve().parents[4]


def test_indexing_real_embeddings_and_versions(tmp_path, monkeypatch) -> None:
    root = _project_root()

    collection_name = f"itest_indexing_{uuid.uuid4().hex}"

    ctx = Runtime.from_config(make_demo_config(tmp_path))
    orchestrator = IndexingOrchestrator(ctx)

    for adapter in ctx.ds_registry.list_adapters():
        orchestrator.index_datasource(adapter)

    datasource_id = ctx.ds_registry.list_ids()[0]
    entries = ctx.index.get(ctx.namespace, Filter(eq={"datasource_id": datasource_id}), with_vectors=True)
    assert entries, "Expected entries after indexing."
    doc = entries[0].text
    stored_embedding = np.array(entries[0].vector)
    computed_embedding = np.array(
        ctx.embedder.embed_documents([doc])[0]
    )
    similarity = _cosine_similarity(stored_embedding, computed_embedding)
    assert similarity >= 0.999, f"Embedding similarity too low: {similarity:.6f}"

    for ds_id in ctx.ds_registry.list_ids():
        live = ctx.vector_store.schema_version(ds_id)
        docs = ctx.index.get(ctx.namespace, Filter(eq={"datasource_id": ds_id}))
        assert docs, f"Expected entries for datasource {ds_id}."
        assert all(e.metadata["schema_version"] == live for e in docs)


def test_indexing_eviction_with_schema_change(tmp_path, monkeypatch) -> None:
    from nl2sql.config import demo_config

    base_ds = demo_config().datasources[0].model_dump(mode="json", exclude_defaults=True,
                                                       exclude={"sample_questions"})
    base_db_path = Path(base_ds["connection"]["database"])
    one_version = {"schema": {"max_versions": 1}}

    def _with_database(database: Path) -> dict:
        return {**base_ds, "connection": {"database": database.as_posix()}}

    ctx1 = Runtime.from_config(make_demo_config(tmp_path, datasources=[_with_database(base_db_path)],
                                                settings=one_version))
    orchestrator_1 = IndexingOrchestrator(ctx1)
    adapter_1 = ctx1.ds_registry.list_adapters()[0]
    orchestrator_1.index_datasource(adapter_1)
    version_1 = ctx1.vector_store.schema_version(adapter_1.datasource_id)

    modified_db = tmp_path / "modified.db"
    modified_db.write_bytes(base_db_path.read_bytes())
    with sqlite3.connect(str(modified_db)) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS extra_table (id INTEGER)")
        conn.commit()

    ctx2 = Runtime.from_config(make_demo_config(tmp_path, datasources=[_with_database(modified_db)],
                                                settings=one_version))
    orchestrator_2 = IndexingOrchestrator(ctx2)
    adapter_2 = ctx2.ds_registry.list_adapters()[0]
    orchestrator_2.index_datasource(adapter_2)
    version_2 = ctx2.vector_store.schema_version(adapter_2.datasource_id)

    assert version_1 != version_2
    # Newest first; the replaced version stays for runs that pinned it, past max_versions.
    versions = ctx2.state.list_versions(adapter_2.datasource_id)
    assert versions == [version_2, version_1]

    # The live build is the new one; the build it replaced is kept for runs in flight.
    live = ctx2.index.pointers(ctx2.namespace)[adapter_2.datasource_id]
    entries = ctx2.index.get(ctx2.namespace, Filter(eq={"build_id": live.build_id}))
    assert entries and all(e.metadata["schema_version"] == version_2 for e in entries)
