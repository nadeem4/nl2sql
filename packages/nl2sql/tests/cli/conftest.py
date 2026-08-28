from __future__ import annotations

import os

import pytest

from nl2sql.common.settings import settings


@pytest.fixture(autouse=True)
def restore_global_state():
    """Isolate tests from the process environment and the settings singleton.

    ``index_demo_data`` and the ``--env`` flag both mutate ``os.environ`` and the
    module-level ``settings`` object on purpose, so every test restores them.
    """
    env_snapshot = dict(os.environ)
    settings_snapshot = dict(settings.__dict__)
    yield
    os.environ.clear()
    os.environ.update(env_snapshot)
    settings.__dict__.clear()
    settings.__dict__.update(settings_snapshot)
