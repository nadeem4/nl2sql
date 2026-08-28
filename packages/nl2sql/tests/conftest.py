import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
CORE_SRC = ROOT / "packages" / "core" / "src"
SDK_SRC = ROOT / "packages" / "adapter-sdk" / "src"

for path in (CORE_SRC, SDK_SRC):
    sys.path.insert(0, str(path))


def pytest_collection_modifyitems(config, items):
    """Mark the core integration tests as needing external resources.

    Everything under ``tests/integration/`` needs something a bare checkout
    does not have: the generated demo SQLite databases, or the downloaded ONNX
    embedding model. That is what ``integration`` means. Whether a test also
    needs a paid API key is a separate axis, carried by the ``llm`` marker that
    individual modules declare for themselves.

    Scoped to this package only -- the sqlite-backed integration tests in the
    adapter packages need none of that and stay selected.
    """
    for item in items:
        if "packages/core/tests/integration/" in str(item.fspath).replace("\\", "/"):
            item.add_marker(pytest.mark.integration)
