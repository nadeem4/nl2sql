from __future__ import annotations

import shutil
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from nl2sql.common.errors import ErrorSeverity
from nl2sql.common.settings import settings
from nl2sql.context import NL2SQLContext
from nl2sql.pipeline.nodes.executor.node import ExecutorNode
from nl2sql.pipeline.nodes.generator.schemas import GeneratorResponse
from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery
from nl2sql.pipeline.state import SubgraphExecutionState


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


@pytest.fixture(scope="module")
def demo_env() -> SimpleNamespace:
    root = _project_root()
    _skip_if_missing_demo_dbs(root)

    tmp_dir = Path(tempfile.mkdtemp(prefix="executor_real_data_"))
    secrets_path = _write_empty_secrets(tmp_dir)

    monkeypatch = pytest.MonkeyPatch()
    collection_name = f"itest_executor_{uuid.uuid4().hex}"
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


def test_executor_real_data(demo_env) -> None:
    datasources = _load_demo_datasources(demo_env.root)
    node = ExecutorNode(demo_env.ctx)

    for ds in datasources:
        ds_id = ds["id"]
        state = SubgraphExecutionState(
            trace_id="t",
            sub_query=SubQuery(id="sq1", datasource_id=ds_id, intent="q"),
            generator_response=GeneratorResponse(sql_draft="SELECT 1"),
        )
        result = node(state)
        errors = result["errors"]
        assert all(
            e.severity not in (ErrorSeverity.CRITICAL, ErrorSeverity.ERROR)
            or "ResultColumn" in e.message
            for e in errors
        )
