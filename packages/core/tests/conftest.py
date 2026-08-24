import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
CORE_SRC = ROOT / "packages" / "core" / "src"
SDK_SRC = ROOT / "packages" / "adapter-sdk" / "src"

for path in (CORE_SRC, SDK_SRC):
    sys.path.insert(0, str(path))


def pytest_collection_modifyitems(config, items):
    """Mark the core integration tests so CI can skip them.

    They need a live demo database, a populated vector store and a real
    OPENAI_API_KEY, so CI runs with ``-m "not integration"``. Scoped to this
    package only -- the sqlite-backed integration tests in the adapter
    packages need none of that and stay selected.
    """
    for item in items:
        if "packages/core/tests/integration/" in str(item.fspath).replace("\\", "/"):
            item.add_marker(pytest.mark.integration)
