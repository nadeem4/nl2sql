"""Session-wide test fixtures.

Lives at the repo root so it applies to every path in ``pytest.ini``'s
``testpaths``, not just one package.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_embedding_service_cache():
    """Clear the process-wide embedder cache around every test.

    ``EmbeddingService`` memoises its embedder on the class, and ``monkeypatch``
    does not unwind class attributes. Without this, whichever test first builds
    an embedder while an API key happens to be set leaves that instance in place
    for the rest of the session, so tests that construct a context pass or fail
    depending on the order they ran in.
    """
    from nl2sql.indexing.embeddings import EmbeddingService

    EmbeddingService.reset()
    try:
        yield
    finally:
        EmbeddingService.reset()


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
