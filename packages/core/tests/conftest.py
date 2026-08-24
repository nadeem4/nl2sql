import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
CORE_SRC = ROOT / "packages" / "core" / "src"
SDK_SRC = ROOT / "packages" / "adapter-sdk" / "src"

for path in (CORE_SRC, SDK_SRC):
    sys.path.insert(0, str(path))


def pytest_collection_modifyitems(config, items):
    """Mark everything under an ``integration`` directory as integration.

    These tests need a live demo database, a populated vector store and a real
    OPENAI_API_KEY, so CI runs with ``-m "not integration"``.
    """
    for item in items:
        parts = str(item.fspath).replace("\\", "/").split("/")
        if "integration" in parts:
            item.add_marker(pytest.mark.integration)
