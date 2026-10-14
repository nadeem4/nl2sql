from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Optional

import pytest
import yaml

from nl2sql.datasets.demo import DEMO_QUESTIONS_BY_DATASOURCE
from nl2sql.runtime import Runtime
from nl2sql.testing.config import make_demo_config
from nl2sql.indexing.orchestrator import IndexingOrchestrator
from nl2sql.testing.fake_llm import FakeLLMServer

CLI = [sys.executable, "-m", "nl2sql.cli.main"]

# The variables a 0.4 run read; none of them means anything to 0.5, so a test
# environment drops them rather than let one leak in from the shell.
PROVIDER_KEYS = ("OPENAI_API_KEY", "OPENROUTER_API_KEY", "ANTHROPIC_API_KEY")


def _base_env() -> dict:
    env = {k: v for k, v in os.environ.items() if k not in PROVIDER_KEYS + ("NL2SQL_CONFIG",)}
    env.update({"NO_COLOR": "1", "TERM": "dumb", "COLUMNS": "200"})
    return env


@pytest.fixture(scope="session")
def demo_project(tmp_path_factory):
    """A demo project written by ``nl2sql init --demo`` and indexed locally (no key).

    Its index, verified queries included, lives in ``<root>/.nl2sql``. Tests
    run against variants of its ``nl2sql.yaml`` written by ``project_config``.
    """
    root = tmp_path_factory.mktemp("demo")
    env = _base_env()
    subprocess.run(CLI + ["init", "--demo", str(root)], env=env, check=True, timeout=300)
    subprocess.run(CLI + ["--config", str(root / "nl2sql.yaml"), "index"], cwd=root, env=env,
                   check=True, timeout=900)
    return root


def fake_llm_section(base_url: str, model: str = "gpt-4o", **fields: Any) -> dict:
    """The ``llm`` section pointing every agent at a ``FakeLLMServer``."""
    return {"provider": "openai", "model": model, "temperature": 0.0, "base_url": base_url,
            "api_key": "${env:OPENAI_API_KEY}", **fields}


def project_config(root: Path, name: str, *, llm: dict, curated: Any = False,
                   settings: Optional[Dict[str, Any]] = None, policies: Optional[dict] = None,
                   storage: Optional[Path] = None, api: Optional[dict] = None) -> Path:
    """Write ``<root>/<name>``: an ``nl2sql.yaml`` over the demo project's datasources and index.

    The demo's datasources, embeddings and policies are included by file. The
    plan cache is off unless ``settings`` turns it on: the demo's state store
    is shared by every test in the session, and a plan one test cached would
    skip the planner in the next. Curated answers are off unless ``curated``:
    ``True`` includes the demo's ``curated.yaml``, a dict is the section inline.
    ``storage`` names a private store (see ``private_storage``); without it the
    demo's own ``.nl2sql`` (with the session's index) is used.
    """
    merged: Dict[str, Any] = {"pipeline": {"plan_cache": False}}
    for group, values in (settings or {}).items():
        merged[group] = {**merged.get(group, {}), **values} if isinstance(values, dict) else values
    document: Dict[str, Any] = {
        "version": 2,
        "datasources": {"file": "datasources/"},
        "llm": llm,
        "embeddings": {"file": "embeddings.yaml"},
        "policies": policies if policies is not None else {"file": "policies.yaml"},
        "storage": {"path": str(storage)} if storage is not None else {"file": "storage.yaml"},
        "settings": merged,
    }
    if isinstance(curated, dict):
        document["curated"] = curated
    elif curated:
        document["curated"] = {"file": "curated.yaml"}
    if api is not None:
        document["api"] = api
    path = root / name
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return path


def private_storage(root: Path, target: Path) -> Path:
    """A copy of the demo's index and state store at ``target``, for a test that writes traces or plans."""
    shutil.copytree(root / ".nl2sql" / "index", target / "index")
    shutil.copy2(root / ".nl2sql" / "state.db", target / "state.db")
    return target


@pytest.fixture
def fake_llm(demo_project):
    """``fake_llm(rules)`` serves ``rules``; returns ``(server, env)`` and writes ``fake.yaml``."""
    servers = []

    def _make(rules):
        server = FakeLLMServer(rules).start()
        servers.append(server)
        project_config(demo_project, "fake.yaml", llm=fake_llm_section(server.base_url))
        env = _base_env()
        env["OPENAI_API_KEY"] = "sk-fake"
        return server, env

    yield _make
    for s in servers:
        s.stop()


def run_cli(root, env, *args, config="fake.yaml", timeout=300):
    # The CLI writes UTF-8 (``configure_output_encoding`` forces it). Decoding
    # with the locale encoding instead raises UnicodeDecodeError inside
    # subprocess' reader thread on a Windows box whose code page is cp1252, and
    # leaves ``stdout`` as None, so name the encoding here.
    return subprocess.run(CLI + ["--config", str(Path(root) / config), *args], cwd=root, env=env,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout)


@pytest.fixture(scope="module")
def demo_env() -> SimpleNamespace:
    """A runtime over the packaged demo databases, indexed into a temp folder."""
    tmp_dir = Path(tempfile.mkdtemp(prefix="pipeline_e2e_"))
    ctx = Runtime.from_config(make_demo_config(tmp_dir, settings={"pipeline": {"timeout_s": 180, "plan_cache": False}}))

    orchestrator = IndexingOrchestrator(ctx)
    for adapter in ctx.ds_registry.list_adapters():
        orchestrator.index_datasource(adapter)

    env = SimpleNamespace(ctx=ctx, tmp_dir=tmp_dir)
    try:
        yield env
    finally:
        ctx.close()
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.fixture(scope="module")
def sample_questions() -> dict[str, list[str]]:
    return {ds: list(questions) for ds, questions in DEMO_QUESTIONS_BY_DATASOURCE.items()}
