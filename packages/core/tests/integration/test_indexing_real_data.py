from __future__ import annotations

import copy
import sqlite3
import uuid
from pathlib import Path

import numpy as np
import pytest
import yaml

from nl2sql.common.settings import settings
from nl2sql.context import NL2SQLContext
from nl2sql.indexing.embeddings import EmbeddingService
from nl2sql.indexing.orchestrator import IndexingOrchestrator


def _cosine_similarity(left: np.ndarray, right: np.ndarray) -> float:
    left_norm = np.linalg.norm(left)
    right_norm = np.linalg.norm(right)
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return float(np.dot(left, right) / (left_norm * right_norm))


def _project_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _write_empty_secrets(tmp_path: Path) -> Path:
    secrets_path = tmp_path / "secrets.yaml"
    secrets_path.write_text("version: 1\nproviders: []\n", encoding="utf-8")
    return secrets_path


def _demo_config_paths(
    root: Path, secrets_config_path: Path, ds_config_path: Path | None = None
) -> dict[str, Path]:
    return {
        "ds_config_path": ds_config_path or root / "configs" / "datasources.demo.yaml",
        "llm_config_path": root / "configs" / "llm.demo.yaml",
        "policies_config_path": root / "configs" / "policies.demo.json",
        "secrets_config_path": secrets_config_path,
    }


def _load_demo_datasources(root: Path) -> list[dict]:
    config_path = root / "configs" / "datasources.demo.yaml"
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    return payload.get("datasources", [])


def _demo_db_paths(root: Path) -> list[Path]:
    db_paths = []
    for datasource in _load_demo_datasources(root):
        connection = datasource.get("connection") or {}
        database = connection.get("database")
        if database:
            db_paths.append(root / database)
    return db_paths


def _skip_if_missing_demo_dbs(root: Path) -> None:
    missing = [str(path) for path in _demo_db_paths(root) if not path.exists()]
    if missing:
        pytest.skip(f"Missing demo databases: {', '.join(missing)}")


def _write_datasource_config(
    tmp_path: Path, base_datasource: dict, database_path: Path
) -> Path:
    datasource = copy.deepcopy(base_datasource)
    datasource["connection"]["database"] = str(database_path)
    config = {"version": 1, "datasources": [datasource]}
    config_path = tmp_path / f"datasources_{uuid.uuid4().hex}.yaml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return config_path


def test_indexing_real_embeddings_and_versions(tmp_path, monkeypatch) -> None:
    root = _project_root()
    _skip_if_missing_demo_dbs(root)
    secrets_path = _write_empty_secrets(tmp_path)

    collection_name = f"itest_indexing_{uuid.uuid4().hex}"
    vector_store_path = tmp_path / "chroma"
    schema_store_path = tmp_path / "schema_store.db"

    monkeypatch.setattr(settings, "vector_store_collection_name", collection_name)
    monkeypatch.setattr(settings, "vector_store_path", str(vector_store_path))
    monkeypatch.setattr(settings, "schema_store_backend", "sqlite")
    monkeypatch.setattr(settings, "schema_store_path", str(schema_store_path))
    monkeypatch.setattr(settings, "schema_store_max_versions", 3)

    ctx = NL2SQLContext(
        **_demo_config_paths(root, secrets_path),
        vector_store_path=vector_store_path,
    )
    orchestrator = IndexingOrchestrator(ctx)

    for adapter in ctx.ds_registry.list_adapters():
        orchestrator.index_datasource(adapter)

    collection = ctx.vector_store.vectorstore._collection
    datasource_id = ctx.ds_registry.list_ids()[0]
    result = collection.get(
        where={"datasource_id": datasource_id},
        include=["documents", "embeddings", "metadatas"],
    )
    assert result["documents"], "Expected documents after indexing."
    doc = result["documents"][0]
    stored_embedding = np.array(result["embeddings"][0])
    computed_embedding = np.array(
        EmbeddingService.get_embeddings().embed_documents([doc])[0]
    )
    similarity = _cosine_similarity(stored_embedding, computed_embedding)
    assert similarity >= 0.999, f"Embedding similarity too low: {similarity:.6f}"

    for ds_id in ctx.ds_registry.list_ids():
        latest = ctx.schema_store.get_latest_version(ds_id)
        docs = collection.get(
            where={"datasource_id": ds_id}, include=["metadatas"]
        )
        assert docs["metadatas"], f"Expected docs for datasource {ds_id}."
        assert all(m["schema_version"] == latest for m in docs["metadatas"])


def test_indexing_eviction_with_schema_change(tmp_path, monkeypatch) -> None:
    root = _project_root()
    _skip_if_missing_demo_dbs(root)
    secrets_path = _write_empty_secrets(tmp_path)

    datasources = _load_demo_datasources(root)
    assert datasources, "Demo datasources missing from config."
    base_ds = datasources[0]
    base_db_path = root / base_ds["connection"]["database"]

    collection_name = f"itest_eviction_{uuid.uuid4().hex}"
    vector_store_path = tmp_path / "chroma"
    schema_store_path = tmp_path / "schema_store.db"

    monkeypatch.setattr(settings, "vector_store_collection_name", collection_name)
    monkeypatch.setattr(settings, "vector_store_path", str(vector_store_path))
    monkeypatch.setattr(settings, "schema_store_backend", "sqlite")
    monkeypatch.setattr(settings, "schema_store_path", str(schema_store_path))
    monkeypatch.setattr(settings, "schema_store_max_versions", 1)

    config_path_1 = _write_datasource_config(tmp_path, base_ds, base_db_path)
    ctx1 = NL2SQLContext(
        **_demo_config_paths(root, secrets_path, ds_config_path=config_path_1),
        vector_store_path=vector_store_path,
    )
    orchestrator_1 = IndexingOrchestrator(ctx1)
    adapter_1 = ctx1.ds_registry.list_adapters()[0]
    orchestrator_1.index_datasource(adapter_1)
    version_1 = ctx1.schema_store.get_latest_version(adapter_1.datasource_id)

    modified_db = tmp_path / "modified.db"
    modified_db.write_bytes(base_db_path.read_bytes())
    with sqlite3.connect(str(modified_db)) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS extra_table (id INTEGER)")
        conn.commit()

    config_path_2 = _write_datasource_config(tmp_path, base_ds, modified_db)
    ctx2 = NL2SQLContext(
        **_demo_config_paths(root, secrets_path, ds_config_path=config_path_2),
        vector_store_path=vector_store_path,
    )
    orchestrator_2 = IndexingOrchestrator(ctx2)
    adapter_2 = ctx2.ds_registry.list_adapters()[0]
    orchestrator_2.index_datasource(adapter_2)
    version_2 = ctx2.schema_store.get_latest_version(adapter_2.datasource_id)

    assert version_1 != version_2
    versions = ctx2.schema_store.list_versions(adapter_2.datasource_id)
    assert versions == [version_2]

    collection = ctx2.vector_store.vectorstore._collection
    old_docs = collection.get(
        where={
            "$and": [
                {"datasource_id": adapter_2.datasource_id},
                {"schema_version": version_1},
            ]
        },
    )
    assert not old_docs["ids"]
