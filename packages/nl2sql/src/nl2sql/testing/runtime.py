"""``fake_runtime``: a stand-in for the engine's runtime, for tests of one node or service."""
from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Optional, Union

from nl2sql.config import LLM, Config, Embeddings, Limits, Policies, Settings, Storage
from nl2sql.events import EventBus
from nl2sql.execution.artifacts import ArtifactStore
from nl2sql.policies import PolicyEngine
from nl2sql.storage.files import FilesTraces
from nl2sql.storage.local import LocalResults


class FakeRuntime(SimpleNamespace):
    """Reads ``settings`` and ``tenant_id`` from ``config``, as the runtime does."""

    @property
    def settings(self) -> Settings:
        return self.config.settings

    @property
    def tenant_id(self) -> str:
        return self.config.settings.tenant_id

    @property
    def policy_engine(self) -> PolicyEngine:
        """Built from ``config.policies`` and ``ds_registry``, as the runtime builds it."""
        return PolicyEngine(self.config.policies, getattr(self, "ds_registry", None))

    def derive(self, changes: Dict[str, Any]) -> "FakeRuntime":
        """A copy with the derived config, as ``Runtime.derive``; other attributes are shared.

        ``curated`` follows ``changes["curated"]`` when it is given. The LLM
        registry is the same object, since a fake one is a stub.
        """
        from nl2sql.runtime import derived_config

        attrs = {**vars(self), "config": derived_config(self.config, changes)}
        if "curated" in changes:
            attrs["curated"] = changes["curated"]
        return FakeRuntime(**attrs)


def fake_runtime(*, settings: Union[Settings, dict, None] = None, policies: Union[Policies, dict, None] = None,
                 results: Optional[Union[str, Path]] = None, **attrs: Any) -> FakeRuntime:
    """A runtime with a real ``config`` and results store, and whatever else a test passes.

    ``settings`` and ``policies`` are the config's sections, written as in YAML
    (``{"results": {"row_limit": 5}}``) or as models; both default to their
    defaults. ``results`` is the results folder (``ctx.artifact_store``, on a
    ``local`` store), a new temporary folder by default. ``traces`` is the
    trace store, a ``files`` store on ``traces_dir`` (a folder), itself a new
    temporary folder by default. Every other keyword becomes an attribute:
    ``ds_registry``, ``llm_registry``, ``vector_store`` and so on. ``policy_engine`` is
    built from ``policies`` (which read nothing by default; ``{"allow_all": True}``
    reads everything). ``bus`` is a new ``EventBus`` of its own.
    """
    settings = settings if isinstance(settings, Settings) else Settings.model_validate(settings or {})
    policies = policies if isinstance(policies, Policies) else Policies.model_validate(policies or {})
    folder = Path(results) if results is not None else Path(tempfile.mkdtemp(prefix="nl2sql-results-"))
    config = Config(datasources=[], llm=LLM(provider="openai", model="gpt-test"),
                    embeddings=Embeddings(provider="local"), policies=policies,
                    storage=Storage(path=folder.parent), settings=settings)
    attrs.setdefault("artifact_store", ArtifactStore(LocalResults(folder)))
    attrs.setdefault("bus", EventBus())
    if "traces" not in attrs:
        traces_dir = attrs.pop("traces_dir", None) or Path(tempfile.mkdtemp(prefix="nl2sql-traces-"))
        attrs["traces"] = FilesTraces(Path(traces_dir))
    return FakeRuntime(config=config, **attrs)


def stub_ds_registry(adapter: Any, *, limits: Optional[Limits] = None, **attrs: Any) -> SimpleNamespace:
    """A datasource registry that gives ``adapter`` for every id, and ``limits`` as each datasource's.

    For a test of one node: the nodes read a datasource's ``row_limit`` from
    the registry, not from its adapter. Other keywords become attributes
    (``supports=...``).
    """
    datasource = SimpleNamespace(limits=limits or Limits())
    return SimpleNamespace(get_adapter=lambda _id: adapter, get_datasource=lambda _id: datasource, **attrs)
