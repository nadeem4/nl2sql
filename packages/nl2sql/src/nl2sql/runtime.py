"""The runtime: everything one engine builds from its ``Config``.

``Runtime.from_config(cfg, registry)`` resolves every name the config uses
through the registry, builds the secret manager, the adapters (not
connected), the LLM clients (no model call), the embedder and the local
stores under ``storage.path``, and raises one ``ConfigError`` listing every
problem. Every node, store and service is handed the runtime. See
docs/architecture/configuration_v2.md, sections 4, 6 and 8.

Nothing here is shared between engines, and nothing is process-global: every
module reads its settings from the runtime it is handed (``ctx.settings``) or
takes them as parameters, and nothing writes the environment.
"""
from __future__ import annotations

import copy
from threading import RLock
from typing import TYPE_CHECKING, Any, Dict, List, Mapping, Optional, Union

from nl2sql_sdk import ConnectionCheck

from nl2sql.check import DatasourceStatus
from nl2sql.common.logger import get_logger
from nl2sql.config import Config, ConfigError
from nl2sql.config.errors import Problem
from nl2sql.config.models import PolicyCheckConfig
from nl2sql.policies import PolicyEngine
from nl2sql.registry import Registry, RegistryError

if TYPE_CHECKING:
    from nl2sql.policies import UserContext
    from nl2sql.config import LLM, Datasource
    from nl2sql.embeddings import EmbeddingInfo
    from nl2sql.llm.llm_set import LLMChange

logger = get_logger(__name__)
llm_logger = get_logger("nl2sql.llm")


class Runtime:
    """One engine's config, components and stores. Built with ``Runtime.from_config``."""

    config: Config
    registry: Registry

    @classmethod
    def from_config(cls, config: Config, registry: Optional[Registry] = None) -> "Runtime":
        """Load and validate everything ``config`` names, and build the runtime. Never connects.

        ``registry`` defaults to ``Registry.default()``.

        Raises:
            ConfigError: every problem found: an unknown or uninstalled name,
                a secret provider that cannot start, a datasource whose
                adapter rejects its connection settings, an embeddings
                provider without its key, or curated answers that do not
                match the datasources.
        """
        self = cls.components(config, registry)
        self._build_stores()
        return self

    @classmethod
    def components(cls, config: Config, registry: Optional[Registry] = None) -> "Runtime":
        """Step 2b of loading without opening the stores: what ``nl2sql config check`` builds.

        Loads every name ``config`` uses through the registry and builds the
        secret manager, the policy engine (with its policy check), the adapters (not connected), the
        LLM clients (no model call), the embedder and the four stores
        named in ``storage`` (they open nothing yet). Nothing is written
        under ``storage.path`` and no process global is touched. The result
        can ``check_datasources()`` and ``close()``; it cannot answer.

        Raises:
            ConfigError: as ``from_config``. Each problem names the file it is
                in (``config.locate``).
        """
        from nl2sql.datasources import DatasourceRegistry
        from nl2sql.llm import LLMRegistry
        from nl2sql.secrets import SecretManager

        self = cls()
        self.registry = registry if registry is not None else Registry.default()
        self._lock = RLock()
        self._closed = False
        source = "nl2sql.yaml" if config.folder is not None else "Config"
        problems: List[Problem] = []

        def problem(path: str, message: str, fix: Optional[str] = None) -> None:
            file, inner = config.locate(path) if config.folder is not None else (source, path)
            problems.append(Problem(file=file, path=inner, message=message, fix=fix))

        # Secrets first: datasources, LLM keys and embeddings resolve through them.
        self.secret_manager = SecretManager(self.registry)
        for i, provider in enumerate(config.secrets.providers):
            try:
                self.secret_manager.configure([provider])
            except ValueError as e:
                problem(f"secrets.providers[{i}]", str(e))

        self.ds_registry = DatasourceRegistry(self.secret_manager, self.registry, base_dir=config.folder)
        for i, datasource in enumerate(config.datasources):
            try:
                self.ds_registry.register_datasource(datasource)
            except RegistryError as e:
                problem(f"datasources[{i}].type", str(e))
            except Exception as e:
                problem(f"datasources[{i}].connection", str(e) or type(e).__name__)

        self.llm_registry = LLMRegistry(self.secret_manager, self.registry)
        llm_problems = _llm_provider_problems(config.llm, self.registry, source)
        for p in llm_problems:
            problem(p.path, p.message)
        if not llm_problems:
            # Clients are built now (no model call); a key that does not
            # resolve is check()'s to report, not the constructor's.
            self.llm_registry.swap(self.llm_registry.build_from(config.llm))

        self.embedder = None
        # Shared with derived runtimes, so the dimension is probed at most once per engine.
        self._embedding: Dict[str, Any] = {}
        try:
            self.embedder = self._build_embedder(config)
        except (RegistryError, ValueError) as e:
            problem("embeddings.provider" if isinstance(e, RegistryError) else "embeddings", str(e))

        self.curated = config.curated
        if config.curated is not None:
            from nl2sql.config.curated import check_curated

            dialects = {ds_id: self.ds_registry.get_dialect(ds_id) for ds_id in self.ds_registry.list_ids()}
            for issue in check_curated(config.curated, dialects):
                problem(f"curated.datasources.{issue.datasource_id}", str(issue))

        stores = self._resolve_stores(config, problem)
        self.policy_check = None
        if isinstance(config.policies.check, PolicyCheckConfig):
            from nl2sql.policies.check import build_check

            try:
                self.policy_check = build_check(config.policies.check, registry=self.registry,
                                                secrets=self.secret_manager)
            except (RegistryError, ValueError) as e:
                problem("policies.check", str(e))
        if problems:
            self.llm_registry.close()
            raise ConfigError(problems, source=source)

        self._config = config
        self.policy_engine = PolicyEngine(config.policies, self.ds_registry, self.policy_check)
        self.index, self.state, self.traces, self.results = stores
        return self

    # ------------------------------------------------------------ building

    def _build_embedder(self, config: Config):
        """The embedder, and what it is (``EmbeddingInfo``, its dimension maybe unknown). No model call."""
        from nl2sql.embeddings import EmbeddingInfo

        embeddings = config.embeddings
        if embeddings.instance is not None:
            instance = embeddings.instance
            model = getattr(instance, "model", None)
            self._embedding["info"] = EmbeddingInfo(
                "instance", model if isinstance(model, str) and model else type(instance).__name__,
                embeddings.dimension)
            return instance
        provider = self.registry.load("embeddings", embeddings.provider)
        if embeddings.api_key:
            embeddings = embeddings.model_copy(
                update={"api_key": self.secret_manager.resolve_object(embeddings.api_key)})
        embedder = provider.create(embeddings)
        self._embedding["info"] = provider.describe(embeddings)
        return embedder

    @property
    def embedding_info(self) -> "EmbeddingInfo":
        """The embedder's provider, model and dimension.

        When neither the config nor the provider knows the dimension, the
        first read embeds one short text to learn it; the answer is kept for
        the engine's life.
        """
        from nl2sql.embeddings import probe

        with self._lock:
            info = self._embedding["info"]
            if info.dimension is None:
                info = self._embedding["info"] = probe(info, self.embedder)
            return info

    def _resolve_stores(self, config: Config, problem) -> List[Any]:
        """The four ``storage`` slots' stores, built through the registry. Writes nothing.

        The engine reads every slot through its interface (``IndexStore``,
        ``StateStore``, ``TraceStore``, ``ResultStore``), so any store works
        in any slot it fills.
        """
        from nl2sql.config.models import STORE_SLOTS
        from nl2sql.storage import build_store

        stores: List[Any] = []
        for slot in STORE_SLOTS:
            store = None
            try:
                store = build_store(slot, getattr(config.storage, slot), folder=config.storage.path,
                                    registry=self.registry, secrets=self.secret_manager)
            except (RegistryError, ValueError) as e:
                problem(f"storage.{slot}", str(e))
            stores.append(store)
        return stores

    def _build_stores(self) -> None:
        from nl2sql.execution import ExecutionStore
        from nl2sql.execution.artifacts import ArtifactStore

        self.state.migrate()
        self.execution_store = ExecutionStore()
        self.artifact_store = ArtifactStore(self.results)

    # ------------------------------------------------------------ the index

    @property
    def namespace(self) -> str:
        """The index namespace of the configured embedding model: ``nl2sql__<model slug>__<dimension>``."""
        from nl2sql.indexing.retrieval import namespace_for

        return namespace_for(self.embedding_info)

    @property
    def vector_store(self):
        """A ``Retriever`` on the index: this run's pinned one, else one reading the live pointers now."""
        pinned = getattr(self, "_pinned_retriever", None)
        return pinned if pinned is not None else self._retriever()

    def _retriever(self):
        from nl2sql.indexing.retrieval import Retriever

        namespace = self.namespace
        return Retriever(self.index, namespace, self.embedder, self.index.pointers(namespace),
                         state=self.state)

    def pin_index(self) -> "Runtime":
        """A run-scoped runtime that reads the index through the pointers as they are now.

        Every step of the run then uses the same build and schema version of
        each datasource, even if a rebuild switches a pointer meanwhile; a
        rebuild keeps the build it replaces, so the run's entries stay. It
        shares everything else with this runtime and is never closed.
        """
        run = copy.copy(self)
        run._pinned_retriever = self._retriever()
        return run

    # ------------------------------------------------------------ reading

    @property
    def config(self) -> Config:
        """The current config. It changes only when a datasource is added or the LLM is replaced."""
        with self._lock:
            return self._config

    @property
    def settings(self):
        """The ``settings`` section: the tuning knobs this engine runs with."""
        return self.config.settings

    @property
    def tenant_id(self) -> str:
        return self.config.settings.tenant_id

    @property
    def closed(self) -> bool:
        return self._closed

    # ------------------------------------------------------------ changing

    def add_datasource(self, datasource: "Datasource") -> None:
        """Builds and registers ``datasource`` (not connected), and adds it to ``config``.

        Raises:
            ValueError: its id is taken, or its adapter cannot be built.
        """
        with self._lock:
            config = self._config.with_datasource(datasource)
            self.ds_registry.register_datasource(datasource)
            self._config = config

    def remove_datasource(self, datasource_id: str) -> None:
        """Takes ``datasource_id`` out of routing and ``config``, and closes its adapter's pool.

        Raises:
            ValueError: it is not configured.
        """
        with self._lock:
            config = self._config.without_datasource(datasource_id)
            self.ds_registry.remove(datasource_id)
            self._config = config

    def replace_llm(self, llm: Union["LLM", Mapping[str, Any]], *, by: Optional["UserContext"] = None,
                    via: str = "python") -> "LLMChange":
        """Swaps in ``llm`` as one step, and logs and audits the change. Never calls a model.

        ``llm`` is validated as the ``llm`` section of ``nl2sql.yaml`` is, and
        every agent's API key must resolve. A mapping is that section as
        written (a request body), checked by the same rules. The new clients are built first;
        then the LLM set and ``config.llm`` are swapped together under the
        engine's lock. Runs in flight finish on the old set.

        Raises:
            ConfigError: a provider is unknown or uninstalled, or an agent's
                key does not resolve. Nothing has changed.
        """
        from nl2sql.config import LLM
        from nl2sql.config.loader import parse_section
        from nl2sql.llm.llm_set import LLMChange

        source = "LLM"
        if isinstance(llm, Mapping):
            llm = parse_section("llm", dict(llm), source=source)
        if not isinstance(llm, LLM):
            raise TypeError(f"replace takes an LLM or the llm section as a mapping, not {type(llm).__name__}.")
        problems = _llm_provider_problems(llm, self.registry, source)
        if problems:
            raise ConfigError(problems, source=source)
        new = self.llm_registry.build_from(llm)
        if new.missing:
            self.llm_registry.discard(new)
            raise ConfigError([Problem(file=source, path=_llm_key_path(name), message=message)
                               for name, message in new.missing.items()], source=source)
        with self._lock:
            old = self.llm_registry.swap(new)
            self._config = self._config.model_copy(update={"llm": llm})
        change = LLMChange(by=_who(by, via), changes=_llm_changes(old, new))
        _log_llm_change(change, tenant_id=self.tenant_id)
        return change

    def derive(self, changes: Dict[str, Any]) -> "Runtime":
        """A run-scoped runtime whose config is this one's with ``changes`` (dotted paths) set.

        Evaluation and trace replay run on one when a run needs different
        settings: ``ctx.derive({"settings.pipeline.plan_cache": False})``. It
        shares this runtime's adapters, embedder, stores and LLM client cache,
        and has its own config, its own curated answers (the derived config's)
        and its own LLM set, built through that cache from the derived ``llm``
        section (which carries any ``llm.replace`` made on this runtime). So
        replacing the run's LLMs leaves this runtime's alone. This runtime
        never changes. The engine owns the shared parts, so a derived runtime
        is never closed.
        """
        config = derived_config(self.config, changes)
        run = copy.copy(self)
        run._lock = RLock()
        run._config = config
        run.curated = config.curated
        run.policy_engine = PolicyEngine(config.policies, self.ds_registry, self.policy_check)
        run.llm_registry = self.llm_registry.derive(config.llm)
        return run

    # ------------------------------------------------------------ lifecycle

    def check_datasources(self) -> List[DatasourceStatus]:
        """Connects to each datasource; a failed one is kept out of routing until it connects again."""
        return [self.check_datasource(datasource.id) for datasource in self.config.datasources]

    def check_datasource(self, datasource_id: str) -> DatasourceStatus:
        """Connects to one datasource: ``ready`` puts it back in routing, ``failed`` keeps it out.

        Raises:
            ValueError: it is not configured.
        """
        datasources = self.config.datasources
        i = next((i for i, d in enumerate(datasources) if d.id == datasource_id), None)
        if i is None:
            raise ValueError(f"Unknown datasource ID: {datasource_id}")
        datasource = datasources[i]
        adapter = self.ds_registry.all_adapters().get(datasource.id)
        try:
            check = adapter.test_connection()
        except Exception as e:
            check = ConnectionCheck(ok=False, message=str(e) or type(e).__name__)
        if check.ok:
            self.ds_registry.mark_available(datasource.id)
            return DatasourceStatus(id=datasource.id, status="ready", required=datasource.required)
        reason = check.message or "The connection test failed."
        self.ds_registry.mark_unavailable(datasource.id, reason)
        return DatasourceStatus(
            id=datasource.id, status="failed", required=datasource.required, stage=check.stage or "connect",
            reason=reason,
            fix=check.fix or f"Check datasources[{i}].connection for '{datasource.id}', and that the database "
                             "is reachable from this machine. The log has the driver's error.")

    def close(self) -> None:
        """Disposes the adapters' pools, closes the LLM clients and the stores. Calling it again does nothing."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self.ds_registry.close()
        self.llm_registry.close()
        for store in (getattr(self, "state", None), getattr(self, "index", None),
                      getattr(self, "traces", None), getattr(self, "results", None)):
            close = getattr(store, "close", None)
            if callable(close):
                try:
                    close()
                except Exception as e:
                    logger.warning(f"Closing {type(store).__name__} failed: {e}")


def _llm_providers(llm: "LLM"):
    """``(path, provider name)`` for every provider the ``llm`` section names."""
    if isinstance(llm.model, str):
        yield "llm.provider", llm.provider
    for agent, override in llm.agents.items():
        model = override.model if "model" in override.model_fields_set else llm.model
        provider = override.provider if "provider" in override.model_fields_set else llm.provider
        if isinstance(model, str) and provider and provider != llm.provider:
            yield f"llm.agents.{agent}.provider", provider


def derived_config(config: Config, changes: Dict[str, Any]) -> Config:
    """``config`` with each dotted path in ``changes`` set: ``{"settings.pipeline.plan_cache": False}``.

    Built with ``model_copy``; ``config`` itself is never changed.
    """
    for path, value in changes.items():
        config = _replaced(config, path.split("."), value)
    return config


def _replaced(model: Any, path: List[str], value: Any) -> Any:
    head, *rest = path
    new = _replaced(getattr(model, head), rest, value) if rest else value
    return model.model_copy(update={head: new})


def _llm_provider_problems(llm: "LLM", registry: Registry, source: str) -> List[Problem]:
    """A problem for every provider in ``llm`` the registry cannot load."""
    problems = []
    for path, provider in _llm_providers(llm):
        try:
            registry.load("llm", provider)
        except RegistryError as e:
            problems.append(Problem(file=source, path=path, message=str(e)))
    return problems


def _config_agent_names() -> Dict[str, str]:
    """The ``llm`` section's agent names, by the name the nodes ask ``get_llm`` for."""
    from typing import get_args

    from nl2sql.config.models import AgentName
    from nl2sql.llm.providers import LLM_AGENTS

    names = {"default": "default"}
    names.update({LLM_AGENTS.get(name, name): name for name in get_args(AgentName)})
    return names


def _llm_key_path(agent: str) -> str:
    """Where in the ``llm`` section agent ``agent``'s key is set."""
    name = _config_agent_names().get(agent, agent)
    return "llm.api_key" if name == "default" else f"llm.agents.{name}.api_key"


def _llm_changes(old, new) -> Dict[str, Dict[str, str]]:
    """Each agent whose effective provider or model differs between two LLM sets."""
    def describe(llms, key: str) -> Optional[str]:
        try:
            return llms.describe(key)
        except ValueError:
            return None

    changes = {}
    for key, name in _config_agent_names().items():
        before, after = describe(old, key), describe(new, key)
        if before != after:
            changes[name] = {"old": before, "new": after}
    return changes


def _who(by: Optional["UserContext"], via: str) -> Dict[str, Any]:
    """Who made a change: the entry point, and the user and their roles when known."""
    who: Dict[str, Any] = {"via": via}
    if by is not None:
        who = {"user_id": by.user_id, "roles": list(by.roles), **who}
    return who


def _log_llm_change(change: "LLMChange", tenant_id: Optional[str]) -> None:
    """One INFO line and one ``llm.changed`` audit record. Neither ever holds a key."""
    from nl2sql.common.event_logger import event_logger

    by = change.by
    who = f"by user={by['user_id']} roles=[{','.join(by['roles'])}] " if "user_id" in by else ""
    what = "; ".join(f"{agent}: {c['old']} -> {c['new']}" for agent, c in change.changes.items())
    llm_logger.info(f"LLM replaced {who}via {by['via']}: {what or 'no provider or model changed'}")
    event_logger.log_event("llm.changed", {"by": dict(by), "changes": dict(change.changes)},
                           tenant_id=tenant_id)

