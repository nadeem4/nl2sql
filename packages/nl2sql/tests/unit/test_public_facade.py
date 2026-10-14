"""The documented public facade must be constructible, and its settings are its config.

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
    """Constructing the facade must not raise; every namespace is wired up."""
    for name in ("datasources", "index", "llm", "policies", "curated", "results", "traces"):
        assert getattr(engine, name) is not None, name


def test_the_settings_are_the_config(engine):
    assert engine.config.settings.tenant_id == "acme"
