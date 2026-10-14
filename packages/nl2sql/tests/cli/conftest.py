from __future__ import annotations

import os

import pytest

from nl2sql.common.settings import settings


@pytest.fixture(autouse=True)
def restore_global_state():
    """Isolate tests from the process environment and the settings singleton.

    Building an engine copies its config into the module-level ``settings``,
    and the ``.env`` next to ``nl2sql.yaml`` and ``nl2sql demo`` write
    ``os.environ``, so every test restores them.
    """
    env_snapshot = dict(os.environ)
    settings_snapshot = dict(settings.__dict__)
    yield
    os.environ.clear()
    os.environ.update(env_snapshot)
    settings.__dict__.clear()
    settings.__dict__.update(settings_snapshot)


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

    ``check()`` reports the index as ``index`` (``ok`` by default, so the demo
    does not index); ``run_query`` raises unless a test replaces it.
    """

    index = "ok"

    def __init__(self, config_path, plan_cache=True):
        from nl2sql.config import load_config

        self.config_path = config_path
        self.config = load_config(config_path)
        self.plan_cache = plan_cache
        self.closed = False

    def check(self):
        from nl2sql.check import CheckReport

        return CheckReport(datasources=[], index=self.index)

    def run_query(self, *args, **kwargs):  # pragma: no cover - replaced where a test needs it
        raise AssertionError("the stub engine does not run queries")

    def list_datasources(self):
        return []

    def list_llms(self):
        return {}

    def close(self):
        self.closed = True


class BuiltEngines(list):
    """The engines ``nl2sql demo`` built, in order. Set ``cls`` to build another stub."""

    cls = StubDemoEngine


@pytest.fixture
def demo_engines(monkeypatch):
    """Every engine ``nl2sql demo`` builds, as ``StubDemoEngine`` (or ``demo_engines.cls``)."""
    built = BuiltEngines()

    def _build(config_path, *, plan_cache=True):
        engine = built.cls(config_path, plan_cache=plan_cache)
        built.append(engine)
        return engine

    monkeypatch.setattr("nl2sql.cli.commands.demo._build_engine", _build)
    return built
