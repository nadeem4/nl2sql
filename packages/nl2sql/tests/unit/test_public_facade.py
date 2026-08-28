"""The documented public facade must be constructible.

``NL2SQL`` is the entry point the README shows and the API lifespan calls
(``app.state.engine = NL2SQL()``), yet nothing exercised it: API tests stub the
engine via ``dependency_overrides`` and core tests build ``NL2SQLContext``
directly. ``SettingsAPI`` had no coverage at all, so a constructor that read a
non-existent ``ConfigManager.settings`` attribute shipped unnoticed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nl2sql import NL2SQL
from nl2sql.common.settings import settings


def _project_root() -> Path:
    return Path(__file__).resolve().parents[4]


@pytest.fixture
def engine(monkeypatch, tmp_path) -> NL2SQL:
    """A facade built against the demo configs, with no network or credentials.

    Mirrors ``test_context_vector_store_settings``: demo config files, a
    temporary vector store, and the key-free local embedder.
    """
    root = _project_root()
    secrets_path = tmp_path / "secrets.yaml"
    secrets_path.write_text("version: 1\nproviders: []\n", encoding="utf-8")

    monkeypatch.setattr(settings, "vector_store_collection_name", "nl2sql_store")
    monkeypatch.setattr(settings, "vector_store_path", "")
    monkeypatch.setattr(settings, "embedding_provider", "local")
    monkeypatch.setattr(settings, "openai_api_key", "")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    return NL2SQL(
        ds_config_path=root / "configs" / "datasources.demo.yaml",
        llm_config_path=root / "configs" / "llm.demo.yaml",
        policies_config_path=root / "configs" / "policies.demo.json",
        secrets_config_path=secrets_path,
        vector_store_path=tmp_path,
    )


def test_facade_constructs(engine):
    """Constructing the facade must not raise; every sub-API is wired up."""
    assert engine.settings is not None
    assert engine.context is not None


def test_current_settings_is_a_populated_dict(engine):
    current = engine.settings.get_current_settings()

    assert isinstance(current, dict)
    assert current
    assert "vector_store_collection_name" in current


def test_get_setting_returns_the_live_value(engine):
    assert engine.get_setting("vector_store_collection_name") == "nl2sql_store"
