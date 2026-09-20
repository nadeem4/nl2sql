import pytest

_INTEGRATION_DIRS = (
    "packages/nl2sql/tests/integration/",
    "packages/nl2sql/tests/e2e/",
)


def pytest_collection_modifyitems(config, items):
    """Mark the engine integration and end-to-end tests as needing external resources.

    Everything under ``tests/integration/`` needs something a bare checkout
    does not have: the generated demo SQLite databases, or the downloaded ONNX
    embedding model. That is what ``integration`` means. ``tests/e2e/`` needs
    the same -- it generates a lite demo project and indexes it with the local
    embedder -- so it carries the marker too. Whether a test also needs a paid
    API key is a separate axis, carried by the ``llm`` marker that individual
    modules declare for themselves; the end-to-end tests deliberately avoid it
    by driving the graph through ``nl2sql.testing.fake_llm``.

    Deliberately matched on the exact engine paths -- the sqlite-backed
    integration tests under ``tests/adapters/`` and ``tests/sqlalchemy_base/``
    need none of that and stay selected.
    """
    for item in items:
        path = str(item.fspath).replace("\\", "/")
        if any(directory in path for directory in _INTEGRATION_DIRS):
            item.add_marker(pytest.mark.integration)
