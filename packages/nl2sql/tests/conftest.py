import pytest


def pytest_collection_modifyitems(config, items):
    """Mark the engine integration tests as needing external resources.

    Everything under ``tests/integration/`` needs something a bare checkout
    does not have: the generated demo SQLite databases, or the downloaded ONNX
    embedding model. That is what ``integration`` means. Whether a test also
    needs a paid API key is a separate axis, carried by the ``llm`` marker that
    individual modules declare for themselves.

    Deliberately matched on the exact engine path -- the sqlite-backed
    integration tests under ``tests/adapters/`` and ``tests/sqlalchemy_base/``
    need none of that and stay selected.
    """
    for item in items:
        if "packages/nl2sql/tests/integration/" in str(item.fspath).replace("\\", "/"):
            item.add_marker(pytest.mark.integration)
