from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from nl2sql.common.settings import settings
from nl2sql.context import NL2SQLContext
from nl2sql.indexing.orchestrator import IndexingOrchestrator
from nl2sql.testing.fake_llm import FakeLLMServer

CLI = [sys.executable, "-m", "nl2sql.cli.main"]


def _base_env() -> dict:
    env = {k: v for k, v in os.environ.items() if k not in ("OPENAI_API_KEY", "ENV", "ENV_FILE_PATH")}
    env.update({"EMBEDDING_PROVIDER": "local", "NO_COLOR": "1", "TERM": "dumb", "COLUMNS": "200"})
    return env


@pytest.fixture(autouse=True)
def _no_shared_plan_cache(monkeypatch):
    """The demo project's schema store is shared by every test in the session.

    With the plan cache on, a question one test asked would skip the planner in
    the next, so the outcome would depend on test order. The cache is off here
    (``_base_env`` copies ``os.environ`` into every CLI run) and
    ``test_plan_cache_fake_llm`` turns it on against a private store copy.
    """
    monkeypatch.setenv("PLAN_CACHE_ENABLED", "false")
    monkeypatch.setattr(settings, "plan_cache_enabled", False)


@pytest.fixture(scope="session")
def demo_project(tmp_path_factory):
    """A generated Chinook demo (one SQLite DB, indexed locally, no key)."""
    root = tmp_path_factory.mktemp("demo")
    subprocess.run(CLI + ["setup", "--demo"], cwd=root, env=_base_env(), check=True, timeout=900)
    return root


@pytest.fixture
def fake_llm(demo_project):
    servers = []

    def _make(rules):
        server = FakeLLMServer(rules).start()
        servers.append(server)
        cfg = {"version": 1, "default": {"provider": "openai", "model": "gpt-4o", "temperature": 0.0,
                                         "base_url": server.base_url, "api_key": "${env:OPENAI_API_KEY}",
                                         "name": "default"}, "agents": {}}
        (demo_project / "configs" / "llm.fake.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
        env = _base_env()
        env["OPENAI_API_KEY"] = "sk-fake"
        return server, env

    yield _make
    for s in servers:
        s.stop()


def run_cli(root, env, *args, timeout=300):
    # The CLI writes UTF-8 (``configure_output_encoding`` forces it). Decoding
    # with the locale encoding instead raises UnicodeDecodeError inside
    # subprocess' reader thread on a Windows box whose code page is cp1252, and
    # leaves ``stdout`` as None, so name the encoding here.
    return subprocess.run(CLI + ["--env", "demo", *args], cwd=root, env=env,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout)


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
    """The demo's guided questions, or nothing if the demo is not generated.

    This is read during *collection*, which happens before ``-m`` deselects
    anything, so a missing file would error the whole module for every
    selection -- including the unit job, which never runs these tests.
    Returning empty leaves the parametrised tests with no cases instead.
    """
    config_path = root / "configs" / "sample_questions.demo.yaml"
    if not config_path.exists():
        return {}
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

    tmp_dir = Path(tempfile.mkdtemp(prefix="pipeline_e2e_"))
    secrets_path = _write_empty_secrets(tmp_dir)

    monkeypatch = pytest.MonkeyPatch()
    collection_name = f"e2e_pipeline_{uuid.uuid4().hex}"
    vector_store_path = tmp_dir / "chroma"
    schema_store_path = tmp_dir / "schema_store.db"

    monkeypatch.setattr(settings, "result_artifact_backend", "local")
    monkeypatch.setattr(settings, "result_artifact_base_uri", tmp_dir.as_posix())
    monkeypatch.setattr(
        settings,
        "result_artifact_path_template",
        "<tenant_id>/<request_id>.parquet",
    )
    monkeypatch.setattr(settings, "vector_store_collection_name", collection_name)
    monkeypatch.setattr(settings, "vector_store_path", str(vector_store_path))
    monkeypatch.setattr(settings, "schema_store_backend", "sqlite")
    monkeypatch.setattr(settings, "schema_store_path", str(schema_store_path))
    monkeypatch.setattr(settings, "schema_store_max_versions", 3)
    monkeypatch.setattr(settings, "global_timeout_sec", 180)

    ctx = NL2SQLContext(
        **_demo_config_paths(root, secrets_path),
        vector_store_path=vector_store_path,
    )

    orchestrator = IndexingOrchestrator(ctx)
    for adapter in ctx.ds_registry.list_adapters():
        orchestrator.index_datasource(adapter)

    env = SimpleNamespace(ctx=ctx, root=root, tmp_dir=tmp_dir)
    try:
        yield env
    finally:
        monkeypatch.undo()
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.fixture(scope="module")
def sample_questions(demo_env) -> dict[str, list[str]]:
    return _load_sample_questions(demo_env.root)
