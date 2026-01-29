from __future__ import annotations

import shutil
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from nl2sql.auth import UserContext
from nl2sql.common.settings import settings
from nl2sql.context import NL2SQLContext
from nl2sql.pipeline.nodes.datasource_resolver.schemas import (
    DatasourceResolverResponse,
    ResolvedDatasource,
)
from nl2sql.pipeline.nodes.decomposer.node import DecomposerNode
from nl2sql.pipeline.nodes.decomposer.schemas import DecomposerResponse
from nl2sql.pipeline.state import GraphState


def _project_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _write_empty_secrets(tmp_path: Path) -> Path:
    secrets_path = tmp_path / "secrets.yaml"
    secrets_path.write_text("version: 1\nproviders: []\n", encoding="utf-8")
    return secrets_path


def _demo_config_paths(root: Path, secrets_config_path: Path) -> dict[str, Path]:
    return {
        "ds_config_path": root / "configs" / "datasources.demo.yaml",
        "llm_config_path": root / "configs" / "llm.demo.yaml",
        "policies_config_path": root / "configs" / "policies.demo.json",
        "secrets_config_path": secrets_config_path,
    }


def _load_sample_questions(root: Path) -> dict[str, list[str]]:
    config_path = root / "configs" / "sample_questions.demo.yaml"
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    return payload or {}


def _demo_db_paths(root: Path) -> list[Path]:
    config_path = root / "configs" / "datasources.demo.yaml"
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    db_paths = []
    for datasource in payload.get("datasources", []):
        connection = datasource.get("connection") or {}
        database = connection.get("database")
        if database:
            db_paths.append(root / database)
    return db_paths


def _skip_if_missing_demo_dbs(root: Path) -> None:
    missing = [str(path) for path in _demo_db_paths(root) if not path.exists()]
    if missing:
        pytest.skip(f"Missing demo databases: {', '.join(missing)}")


@pytest.fixture(scope="module")
def demo_env() -> SimpleNamespace:
    root = _project_root()
    _skip_if_missing_demo_dbs(root)

    tmp_dir = Path(tempfile.mkdtemp(prefix="decomposer_real_data_"))
    secrets_path = _write_empty_secrets(tmp_dir)

    monkeypatch = pytest.MonkeyPatch()
    collection_name = f"itest_decomposer_{uuid.uuid4().hex}"
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

    env = SimpleNamespace(ctx=ctx, root=root, tmp_dir=tmp_dir)
    try:
        yield env
    finally:
        monkeypatch.undo()
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.fixture(scope="module")
def resolver_response(demo_env) -> DatasourceResolverResponse:
    questions = _load_sample_questions(demo_env.root)
    datasource_ids = list(questions.keys())
    resolved = [
        ResolvedDatasource(datasource_id=ds_id, metadata={"datasource_id": ds_id})
        for ds_id in datasource_ids
    ]
    return DatasourceResolverResponse(
        resolved_datasources=resolved,
        allowed_datasource_ids=datasource_ids,
        unsupported_datasource_ids=[],
    )


def _build_queries(questions: dict[str, list[str]]) -> list[str]:
    queries: list[str] = []
    if "manufacturing_ref" in questions and questions["manufacturing_ref"]:
        queries.append(questions["manufacturing_ref"][0])
    for ds_id, items in questions.items():
        if len(items) >= 2:
            queries.append(f"{items[0]} and {items[1]}")
    if "manufacturing_supply" in questions and "manufacturing_history" in questions:
        queries.append(
            f"{questions['manufacturing_supply'][0]} and {questions['manufacturing_history'][0]}"
        )
    return queries


def test_decomposer_queries_structure(demo_env, resolver_response, user_query) -> None:
    node = DecomposerNode(demo_env.ctx)
    state = GraphState(
        user_query=user_query,
        user_context=UserContext(roles=["admin"]),
        datasource_resolver_response=resolver_response,
    )
    result = node(state)

    assert "errors" not in result
    response = result["decomposer_response"]
    assert isinstance(response, DecomposerResponse)

    assert isinstance(response.sub_queries, list)
    assert isinstance(response.combine_groups, list)
    assert isinstance(response.post_combine_ops, list)
    assert isinstance(response.unmapped_subqueries, list)

    for sub_query in response.sub_queries:
        assert sub_query.id
        assert sub_query.datasource_id
        assert sub_query.intent

    subquery_ids = {sq.id for sq in response.sub_queries}
    for group in response.combine_groups:
        for inp in group.inputs:
            assert inp.subquery_id in subquery_ids

    group_ids = {group.group_id for group in response.combine_groups}
    for op in response.post_combine_ops:
        assert op.target_group_id in group_ids


def pytest_generate_tests(metafunc):
    if "user_query" in metafunc.fixturenames:
        root = _project_root()
        questions = _load_sample_questions(root)
        queries = _build_queries(questions)
        metafunc.parametrize("user_query", queries)
