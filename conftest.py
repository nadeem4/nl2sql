"""Session-wide test fixtures.

Lives at the repo root so it applies to every path in ``pytest.ini``'s
``testpaths``, not just one package.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _restore_the_global_settings():
    """Put the process-global ``settings`` back after every test.

    Building an engine copies its config into the global (the staging step in
    docs/architecture/configuration_v2.md, section 8), and ``monkeypatch``
    cannot unwind that. Without this, one test's engine would leave its
    settings in place for the next. Deleted with the global.
    """
    from nl2sql.common.settings import settings

    saved = dict(settings.__dict__)
    try:
        yield
    finally:
        settings.__dict__.clear()
        settings.__dict__.update(saved)


@pytest.fixture(autouse=True)
def _traces_go_to_a_temp_dir(tmp_path_factory, monkeypatch):
    """Keep run traces out of the working tree.

    ``TRACE_MODE`` defaults to ``on_failure`` and the default directory is
    ``traces/`` under the working directory, so every test that drives a
    failing run would otherwise leave a file in the checkout. The variable is
    set as well as the attribute so ``reload_settings()`` keeps it.
    """
    from nl2sql.common.settings import settings

    directory = str(tmp_path_factory.getbasetemp() / "traces")
    monkeypatch.setenv("TRACE_DIR", directory)
    monkeypatch.setattr(settings, "trace_dir", directory)
    yield


@pytest.fixture(autouse=True)
def _artifacts_go_to_a_temp_dir(tmp_path_factory, monkeypatch):
    """Keep result artifacts out of the working tree.

    ``RESULT_ARTIFACT_BASE_URI`` defaults to ``./artifacts``, and every run
    writes its sub-query results and its combined result there.
    """
    from nl2sql.common.settings import settings

    directory = str(tmp_path_factory.getbasetemp() / "artifacts")
    monkeypatch.setenv("RESULT_ARTIFACT_BASE_URI", directory)
    monkeypatch.setattr(settings, "result_artifact_base_uri", directory)
    yield
