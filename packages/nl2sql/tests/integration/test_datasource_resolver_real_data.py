from __future__ import annotations

import copy
import shutil
import sqlite3
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from nl2sql.auth import UserContext
from nl2sql.common.errors import ErrorCode
from nl2sql.common.settings import settings
from nl2sql.context import NL2SQLContext
from nl2sql.indexing.orchestrator import IndexingOrchestrator
from nl2sql.llm.models import AgentConfig
from nl2sql.pipeline.nodes.datasource_resolver.node import DatasourceResolverNode
from nl2sql.pipeline.state import GraphState
from nl2sql.testing.fake_llm import FakeLLMServer, Rule

# The resolver's answerability check is an LLM call; a fake model says every
# question here is about Chinook.
_JUDGE = FakeLLMServer([Rule("AnswerabilityResponse",
                             {"answerable_datasource_ids": ["chinook"], "reason": "Chinook data."})])


def _use_fake_judge(ctx: NL2SQLContext) -> NL2SQLContext:
    ctx.llm_registry.register_llm(AgentConfig(
        provider="openai", model="gpt-4o", name="datasourceresolver",
        base_url=_JUDGE.base_url, api_key="-".join(["fake", "key"]),
    ))
    return ctx


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
    # A second datasource, so the resolver runs its vector search: the schema
    # version mismatch is between an index entry and the store, and with one
    # datasource no index entry is read.
    second = copy.deepcopy(datasource)
    second["id"] = "chinook_copy"
    config = {"version": 1, "datasources": [datasource, second]}
    config_path = tmp_path / f"datasources_{uuid.uuid4().hex}.yaml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return config_path


@pytest.fixture(scope="module")
def indexed_env() -> SimpleNamespace:
    root = _project_root()
    _skip_if_missing_demo_dbs(root)

    tmp_dir = Path(tempfile.mkdtemp(prefix="resolver_real_data_"))
    secrets_path = _write_empty_secrets(tmp_dir)

    monkeypatch = pytest.MonkeyPatch()
    collection_name = f"itest_resolver_{uuid.uuid4().hex}"
    vector_store_path = tmp_dir / "chroma"
    schema_store_path = tmp_dir / "schema_store.db"

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
    _JUDGE.start()
    _use_fake_judge(ctx)

    env = SimpleNamespace(
        ctx=ctx,
        root=root,
        tmp_dir=tmp_dir,
        collection_name=collection_name,
        vector_store_path=vector_store_path,
    )
    try:
        yield env
    finally:
        _JUDGE.stop()
        monkeypatch.undo()
        shutil.rmtree(tmp_dir, ignore_errors=True)


# Chinook is one datasource, so the resolver takes it without a vector search.
# What this still covers is the node against real stores: that every question
# resolves, inside the role's allowlist, with the schema version the store
# holds, and that the answerability check ran on the real description and tables.
@pytest.mark.parametrize(
    ("datasource_id", "user_query"),
    [
        ("chinook", "How many customers do we have, by country?"),
        ("chinook", "Who are the top 5 customers by total spend?"),
        ("chinook", "Which artist has the most albums?"),
        ("chinook", "What is the total revenue per year?"),
        ("chinook", "Which genre sells the most tracks?"),
        ("chinook", "Which employees support the most customers?"),
    ],
)
def test_datasource_resolver_real_queries(
    indexed_env, datasource_id, user_query
) -> None:
    node = DatasourceResolverNode(indexed_env.ctx)
    result = node(
        GraphState(user_query=user_query, user_context=UserContext(roles=["admin"]))
    )

    response = result["datasource_resolver_response"]
    assert response.resolved_datasources
    resolved_ids = {ds.datasource_id for ds in response.resolved_datasources}
    assert resolved_ids
    assert resolved_ids.issubset(set(response.allowed_datasource_ids))
    assert datasource_id in resolved_ids

    for resolved in response.resolved_datasources:
        latest = indexed_env.ctx.schema_store.get_latest_version(resolved.datasource_id)
        assert resolved.schema_version == latest

    prompt = "\n".join(m["content"] for m in _JUDGE.calls[-1]["body"]["messages"])
    assert '"id": "chinook"' in prompt and '"InvoiceLine"' in prompt
    assert prompt.endswith(user_query)


def _build_schema_mismatch_context(indexed_env, tmp_path: Path) -> tuple[NL2SQLContext, str]:
    secrets_path = _write_empty_secrets(tmp_path)
    datasources = _load_demo_datasources(indexed_env.root)
    base_ds = datasources[0]
    base_db_path = indexed_env.root / base_ds["connection"]["database"]

    modified_db = tmp_path / "modified.db"
    modified_db.write_bytes(base_db_path.read_bytes())
    with sqlite3.connect(str(modified_db)) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS extra_table (id INTEGER)")
        conn.commit()

    config_path = _write_datasource_config(tmp_path, base_ds, modified_db)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(settings, "vector_store_collection_name", indexed_env.collection_name)
    monkeypatch.setattr(settings, "vector_store_path", str(indexed_env.vector_store_path))
    monkeypatch.setattr(settings, "schema_store_backend", "sqlite")
    monkeypatch.setattr(settings, "schema_store_path", str(tmp_path / "schema_store.db"))
    monkeypatch.setattr(settings, "schema_store_max_versions", 3)

    ctx = _use_fake_judge(NL2SQLContext(
        **_demo_config_paths(indexed_env.root, secrets_path, ds_config_path=config_path),
        vector_store_path=indexed_env.vector_store_path,
    ))

    adapter = ctx.ds_registry.get_adapter("chinook")
    snapshot = adapter.fetch_schema_snapshot()
    new_version, _ = ctx.schema_store.register_snapshot(snapshot)

    collection = indexed_env.ctx.vector_store.vectorstore._collection
    old_docs = collection.get(
        where={"datasource_id": adapter.datasource_id},
        include=["metadatas"],
    )
    old_version = old_docs["metadatas"][0]["schema_version"]
    assert old_version != new_version

    return ctx, new_version


def test_datasource_resolver_schema_mismatch_warn(indexed_env, tmp_path, monkeypatch) -> None:
    ctx, _new_version = _build_schema_mismatch_context(indexed_env, tmp_path)
    monkeypatch.setattr(
        "nl2sql.pipeline.nodes.datasource_resolver.node.settings.schema_version_mismatch_policy",
        "warn",
    )

    node = DatasourceResolverNode(ctx)
    result = node(
        GraphState(
            user_query="customer invoice totals", user_context=UserContext(roles=["admin"])
        )
    )

    assert result["warnings"]
    assert "Schema version mismatch" in result["warnings"][0]["content"]


def test_datasource_resolver_schema_mismatch_fail(indexed_env, tmp_path, monkeypatch) -> None:
    ctx, _new_version = _build_schema_mismatch_context(indexed_env, tmp_path)
    monkeypatch.setattr(
        "nl2sql.pipeline.nodes.datasource_resolver.node.settings.schema_version_mismatch_policy",
        "fail",
    )

    node = DatasourceResolverNode(ctx)
    result = node(
        GraphState(
            user_query="customer invoice totals", user_context=UserContext(roles=["admin"])
        )
    )

    assert result["errors"]
    assert result["errors"][0].error_code == ErrorCode.INVALID_STATE
