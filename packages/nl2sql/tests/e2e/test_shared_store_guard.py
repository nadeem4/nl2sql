"""No test can open the session demo project's own state store.

Every test gets a private copy (``private_storage``, ``copy_demo``). The shared
``state.db`` was once opened by engines that were never closed, copied while
open and written by CLI runs, all in one session, and CI failed with ``disk
I/O error`` in whichever test pytest-randomly put next.
"""
from __future__ import annotations

import pytest

from nl2sql.storage.sqlite import SqliteState

from .conftest import copy_demo, private_storage


@pytest.mark.e2e
def test_the_shared_demo_store_cannot_be_opened(demo_project):
    store = SqliteState(demo_project / ".nl2sql" / "state.db")
    with pytest.raises(OSError):
        store.migrate()


@pytest.mark.e2e
def test_a_private_store_and_a_copied_project_open(demo_project, tmp_path):
    for path in (private_storage(demo_project, tmp_path / "store"),
                 copy_demo(demo_project, tmp_path / "demo") / ".nl2sql"):
        store = SqliteState(path / "state.db")
        try:
            store.migrate()
            assert store.list_versions("chinook")  # the session's index came with it
        finally:
            store.close()
