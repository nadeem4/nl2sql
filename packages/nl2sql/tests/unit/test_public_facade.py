"""The documented public facade must be constructible, and its settings API must answer.

``NL2SQL`` is the entry point the README shows. API tests stub the engine via
``dependency_overrides``, so these build the real one from a ``make_config``
folder. The constructors, ``check()`` and ``close()`` are covered in
``test_engine_from_config.py``.
"""

from __future__ import annotations

import pytest
from langchain_core.embeddings import DeterministicFakeEmbedding

from nl2sql import NL2SQL, Embeddings
from nl2sql.testing.config import make_config


@pytest.fixture
def engine(tmp_path) -> NL2SQL:
    """A facade over a config with no datasources: no network, credentials or downloads."""
    cfg = make_config(tmp_path, settings={"tenant_id": "acme"})
    return NL2SQL(cfg.model_copy(update={"embeddings": Embeddings(instance=DeterministicFakeEmbedding(size=8))}))


def test_facade_constructs(engine):
    """Constructing the facade must not raise; every sub-API is wired up."""
    assert engine.settings is not None
    assert engine.context is not None


def test_current_settings_is_a_populated_dict(engine):
    current = engine.settings.get_current_settings()

    assert isinstance(current, dict)
    assert current
    assert "tenant_id" in current


def test_get_setting_returns_the_engines_value(engine):
    assert engine.get_setting("tenant_id") == "acme"
