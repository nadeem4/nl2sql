"""``fake_runtime``: a stand-in for the engine's runtime, for tests of one node or service."""
from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional, Union

from nl2sql.config import LLM, Config, Embeddings, Policies, Settings, Storage
from nl2sql.execution.artifacts import ArtifactStore, ArtifactStoreConfig


class FakeRuntime(SimpleNamespace):
    """Reads ``settings`` and ``tenant_id`` from ``config``, as the runtime does."""

    @property
    def settings(self) -> Settings:
        return self.config.settings

    @property
    def tenant_id(self) -> str:
        return self.config.settings.tenant_id


def fake_runtime(*, settings: Union[Settings, dict, None] = None, policies: Union[Policies, dict, None] = None,
                 results: Optional[Union[str, Path]] = None, **attrs: Any) -> FakeRuntime:
    """A runtime with a real ``config`` and results store, and whatever else a test passes.

    ``settings`` and ``policies`` are the config's sections, written as in YAML
    (``{"results": {"row_limit": 5}}``) or as models; both default to their
    defaults. ``results`` is the results folder (``ctx.artifact_store``), a new
    temporary folder by default. Every other keyword becomes an attribute:
    ``ds_registry``, ``rbac``, ``llm_registry``, ``vector_store`` and so on.
    """
    settings = settings if isinstance(settings, Settings) else Settings.model_validate(settings or {})
    policies = policies if isinstance(policies, Policies) else Policies.model_validate(policies or {})
    folder = Path(results) if results is not None else Path(tempfile.mkdtemp(prefix="nl2sql-results-"))
    config = Config(datasources=[], llm=LLM(provider="openai", model="gpt-test"),
                    embeddings=Embeddings(provider="local"), policies=policies,
                    storage=Storage(path=folder.parent), settings=settings)
    attrs.setdefault("artifact_store", ArtifactStore(ArtifactStoreConfig(base_uri=str(folder))))
    return FakeRuntime(config=config, **attrs)
