from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from nl2sql.testing.facade import StubLLMs


@pytest.fixture(autouse=True)
def restore_global_state():
    """Isolate tests from the process environment.

    The CLI's entry points load the ``.env`` next to ``nl2sql.yaml`` into
    ``os.environ`` (``load_env_file``), so every test restores it.
    """
    env_snapshot = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(env_snapshot)


@pytest.fixture
def project_stores(tmp_path, monkeypatch):
    """A ``make_config`` project in ``tmp_path``, named by ``NL2SQL_CONFIG``; returns its store paths.

    The state store is ``<tmp_path>/.nl2sql/state.db`` and the traces are in
    ``<tmp_path>/.nl2sql/traces``, as the engine lays them out.
    """
    from nl2sql.runtime import StorePaths
    from nl2sql.testing.config import make_config

    config = make_config(tmp_path)
    monkeypatch.setenv("NL2SQL_CONFIG", str(tmp_path / "nl2sql.yaml"))
    return StorePaths.under(config.storage.path)


class StubDemoEngine:
    """Stands in for the engine ``nl2sql demo`` builds: the config it was built from, nothing that connects.

    ``check()`` reports the index as ``index_status`` (``ok`` by default, so the
    demo does not index); ``run_query`` raises unless a test replaces it. The
    namespaces the playground reads answer with nothing configured.
    """

    index_status = "ok"

    def __init__(self, config_path, plan_cache=True, api_keys=None):
        from nl2sql.config import load_config

        self.config_path = config_path
        self.config = load_config(config_path)
        self.plan_cache = plan_cache
        self.api_keys = dict(api_keys or {})
        self.closed = False
        self.datasources = SimpleNamespace(list=lambda: [])
        self.llm = StubLLMs({})
        self.index = SimpleNamespace(health=lambda: {"status": self.index_status})

    def check(self):
        from nl2sql.check import CheckReport

        return CheckReport(datasources=[], index=self.index_status)

    def run_query(self, *args, **kwargs):  # pragma: no cover - replaced where a test needs it
        raise AssertionError("the stub engine does not run queries")

    def close(self):
        self.closed = True


class BuiltEngines(list):
    """The engines ``nl2sql demo`` built, in order. Set ``cls`` to build another stub."""

    cls = StubDemoEngine


@pytest.fixture
def demo_engines(monkeypatch):
    """Every engine ``nl2sql demo`` builds, as ``StubDemoEngine`` (or ``demo_engines.cls``)."""
    built = BuiltEngines()

    def _build(config_path, *, plan_cache=True, api_keys=None):
        engine = built.cls(config_path, plan_cache=plan_cache, api_keys=api_keys)
        built.append(engine)
        return engine

    monkeypatch.setattr("nl2sql.cli.commands.demo._build_engine", _build)
    return built
