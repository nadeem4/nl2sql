from __future__ import annotations

import os
from types import SimpleNamespace

import pytest



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
    from nl2sql.testing.config import make_config

    config = make_config(tmp_path)
    monkeypatch.setenv("NL2SQL_CONFIG", str(tmp_path / "nl2sql.yaml"))
    path = config.storage.path
    return SimpleNamespace(state=path / "state.db", traces=path / "traces")
