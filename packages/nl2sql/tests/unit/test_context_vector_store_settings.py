from __future__ import annotations

from pathlib import Path

import pytest

from nl2sql.common.settings import settings
from nl2sql.context import NL2SQLContext


def _project_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _demo_config_paths(root: Path, secrets_config_path: Path) -> dict[str, Path]:
    return {
        "ds_config_path": root / "configs" / "datasources.demo.yaml",
        "llm_config_path": root / "configs" / "llm.demo.yaml",
        "policies_config_path": root / "configs" / "policies.demo.json",
        "secrets_config_path": secrets_config_path,
    }


def _write_empty_secrets(tmp_path: Path) -> Path:
    secrets_path = tmp_path / "secrets.yaml"
    secrets_path.write_text("version: 1\nproviders: []\n", encoding="utf-8")
    return secrets_path


def test_context_requires_vector_store_collection_name(monkeypatch, tmp_path):
    root = _project_root()
    secrets_path = _write_empty_secrets(tmp_path)
    monkeypatch.setattr(settings, "vector_store_collection_name", "")
    monkeypatch.setattr(settings, "vector_store_path", str(tmp_path))

    with pytest.raises(ValueError, match="VECTOR_STORE_COLLECTION"):
        NL2SQLContext(
            **_demo_config_paths(root, secrets_path),
            vector_store_path=tmp_path,
        )


def test_context_requires_vector_store_path_when_not_provided(monkeypatch, tmp_path):
    root = _project_root()
    secrets_path = _write_empty_secrets(tmp_path)
    monkeypatch.setattr(settings, "vector_store_collection_name", "nl2sql_store")
    monkeypatch.setattr(settings, "vector_store_path", "")

    with pytest.raises(ValueError, match="VECTOR_STORE path"):
        NL2SQLContext(
            **_demo_config_paths(root, secrets_path),
            vector_store_path=None,
        )


def test_context_allows_explicit_vector_store_path(monkeypatch, tmp_path):
    root = _project_root()
    secrets_path = _write_empty_secrets(tmp_path)
    monkeypatch.setattr(settings, "vector_store_collection_name", "nl2sql_store")
    monkeypatch.setattr(settings, "vector_store_path", "")
    # Construction succeeds, so the LLM registry is built; a dummy key keeps the
    # test hermetic (no network call is made at construction time).
    # `settings` is a singleton built at import, so the embedder reads this
    # attribute rather than the environment. Patch both: the attribute for the
    # embedder, the variable for the chat client the registry builds.
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    ctx = NL2SQLContext(
        **_demo_config_paths(root, secrets_path),
        vector_store_path=tmp_path,
    )

    assert Path(ctx.vector_store.persist_directory) == tmp_path


def test_context_constructs_without_any_credentials(monkeypatch, tmp_path):
    """A context must build on a machine with no API key configured.

    LLM clients are built on first use, so `nl2sql setup --demo` and indexing
    (key-free since local embeddings landed) no longer need a chat provider key
    just to construct the registries.
    """
    root = _project_root()
    secrets_path = _write_empty_secrets(tmp_path)
    monkeypatch.setattr(settings, "vector_store_collection_name", "nl2sql_store")
    monkeypatch.setattr(settings, "vector_store_path", "")
    monkeypatch.setattr(settings, "embedding_provider", "local")
    monkeypatch.setattr(settings, "openai_api_key", "")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    ctx = NL2SQLContext(
        **_demo_config_paths(root, secrets_path),
        vector_store_path=tmp_path,
    )

    # The configuration is registered; only building the client needs the key.
    assert ctx.llm_registry.get_llm_config("default")["provider"] == "openai"
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        ctx.llm_registry.get_llm("default")
