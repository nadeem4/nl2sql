"""The documented public facade must be constructible.

``NL2SQL`` is the entry point the README shows and the API lifespan calls
(``app.state.engine = NL2SQL()``), yet nothing exercised it: API tests stub the
engine via ``dependency_overrides`` and core tests build ``NL2SQLContext``
directly. ``SettingsAPI`` had no coverage at all, so a constructor that read a
non-existent ``ConfigManager.settings`` attribute shipped unnoticed.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from nl2sql import NL2SQL
from nl2sql.common.settings import reload_settings, settings


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


def _write_demo_env_file(path: Path, tmp_path: Path) -> None:
    """Writes an env file that points the engine at the demo configs."""
    root = _project_root()
    secrets_path = tmp_path / "secrets.yaml"
    secrets_path.write_text("version: 1\nproviders: []\n", encoding="utf-8")
    path.write_text(
        "\n".join(
            [
                "EMBEDDING_PROVIDER=local",
                f"VECTOR_STORE={tmp_path.as_posix()}",
                "VECTOR_STORE_COLLECTION=nl2sql_store",
                f"DATASOURCE_CONFIG={(root / 'configs' / 'datasources.demo.yaml').as_posix()}",
                f"LLM_CONFIG={(root / 'configs' / 'llm.demo.yaml').as_posix()}",
                f"POLICIES_CONFIG={(root / 'configs' / 'policies.demo.json').as_posix()}",
                f"SECRETS_CONFIG={secrets_path.as_posix()}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


# ``NL2SQLContext`` writes ENV / ENV_FILE_PATH into the process environment and
# reloads the settings singleton, so these tests have to put both back by hand:
# ``monkeypatch.delenv(..., raising=False)`` records nothing for a variable that
# was never set, and pytest-randomly makes a leaked singleton a real hazard.
_ENV_VARS_TOUCHED = (
    "ENV",
    "APP_ENV",
    "ENV_FILE_PATH",
    "OPENAI_API_KEY",
    "EMBEDDING_PROVIDER",
    "VECTOR_STORE",
    "VECTOR_STORE_COLLECTION",
    "DATASOURCE_CONFIG",
    "LLM_CONFIG",
    "POLICIES_CONFIG",
    "SECRETS_CONFIG",
)


@pytest.fixture
def clean_env():
    """Isolate the settings singleton: no leaked env vars, always reloaded after.

    Declared before ``monkeypatch`` in every test that uses both, so its
    teardown runs after the working directory has been restored and the
    reloaded settings are the ones the rest of the session expects.
    """
    saved = {name: os.environ.get(name) for name in _ENV_VARS_TOUCHED}
    for name in _ENV_VARS_TOUCHED:
        os.environ.pop(name, None)
    try:
        yield
    finally:
        for name, value in saved.items():
            os.environ.pop(name, None)
            if value is not None:
                os.environ[name] = value
        reload_settings()


def test_engine_from_env_name(clean_env, monkeypatch, tmp_path):
    """``NL2SQL(env="demo")`` reads ``.env.demo`` from the working directory."""
    _write_demo_env_file(tmp_path / ".env.demo", tmp_path)
    monkeypatch.chdir(tmp_path)

    engine = NL2SQL(env="demo")

    assert engine.list_datasources()


def test_engine_from_env_file_path(clean_env, tmp_path):
    """``NL2SQL(env_file=...)`` reads an env file from anywhere."""
    env_file = tmp_path / ".env.demo"
    _write_demo_env_file(env_file, tmp_path)

    engine = NL2SQL(env_file=str(env_file))

    assert engine.list_datasources()
