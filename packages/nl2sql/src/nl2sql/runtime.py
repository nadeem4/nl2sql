"""The runtime: everything one engine builds from its ``Config``.

``Runtime.from_config(cfg, registry)`` resolves every name the config uses
through the registry, builds the secret manager, the adapters (not
connected), the LLM settings (no model call), the embedder and the local
stores under ``storage.path``, and raises one ``ConfigError`` listing every
problem. Every node, store and service is handed the runtime. See
docs/architecture/configuration_v2.md, sections 4, 6 and 8.

Nothing here is shared between engines, apart from one thing that is staged
out over the next pull requests: the process-global ``settings`` that modules
not yet moved onto the runtime still read. ``from_config`` copies the
config's values into it (``_copy_into_global_settings``), so those modules
read this engine's values. The architecture ratchet test lists them.
"""
from __future__ import annotations

from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING, Any, Dict, List, NamedTuple, Optional

from nl2sql.check import DatasourceStatus
from nl2sql.common.logger import get_logger
from nl2sql.config import Config, ConfigError
from nl2sql.config.errors import Problem
from nl2sql.registry import Registry, RegistryError

if TYPE_CHECKING:
    from nl2sql.config import Datasource

logger = get_logger(__name__)

# The vector index's collection: an internal constant, one per index folder.
INDEX_COLLECTION = "nl2sql_store"


class StorePaths(NamedTuple):
    """Where one engine keeps its data."""

    index: Path
    state: Path
    traces: Path
    results: Path

    @classmethod
    def under(cls, path: Path) -> "StorePaths":
        """The layout under ``storage.path``: ``index/``, ``state.db``, ``traces/`` and ``results/``."""
        path = Path(path)
        return cls(index=path / "index", state=path / "state.db", traces=path / "traces", results=path / "results")


class Runtime:
    """One engine's config, components and stores. Built with ``Runtime.from_config``."""

    config: Config
    registry: Registry

    @classmethod
    def from_config(cls, config: Config, registry: Optional[Registry] = None, *,
                    stores: Optional[StorePaths] = None) -> "Runtime":
        """Load and validate everything ``config`` names, and build the runtime. Never connects.

        ``registry`` defaults to ``Registry.default()``. ``stores`` overrides
        the store layout under ``storage.path``; only the 0.4 file entry point
        (``nl2sql._legacy``) passes it.

        Raises:
            ConfigError: every problem found: an unknown or uninstalled name,
                a secret provider that cannot start, a datasource whose
                adapter rejects its connection settings, an embeddings
                provider without its key, or curated answers that do not
                match the datasources.
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
            problems.append(Problem(file=source, path=path, message=message, fix=fix))

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
        llm_ok = True
        for path, provider in _llm_providers(config):
            try:
                self.registry.load("llm", provider)
            except RegistryError as e:
                llm_ok = False
                problem(path, str(e))
        if llm_ok:
            self.llm_registry.configure(config.llm)

        self.embedder = None
        try:
            self.embedder = self._build_embedder(config)
        except (RegistryError, ValueError) as e:
            problem("embeddings.provider" if isinstance(e, RegistryError) else "embeddings", str(e))

        self.curated = config.curated
        if config.curated is not None:
            from nl2sql.configs.curated import check_curated

            dialects = {ds_id: self.ds_registry.get_dialect(ds_id) for ds_id in self.ds_registry.list_ids()}
            for issue in check_curated(config.curated, dialects):
                problem(f"curated.datasources.{issue.datasource_id}", str(issue))

        if problems:
            raise ConfigError(problems, source=source)

        self._config = config
        self.stores = stores or StorePaths.under(config.storage.path)
        _copy_into_global_settings(config, self.stores)
        self._build_stores()
        return self

    # ------------------------------------------------------------ building

    def _build_embedder(self, config: Config):
        embeddings = config.embeddings
        if embeddings.instance is not None:
            return embeddings.instance
        provider = self.registry.load("embeddings", embeddings.provider)
        if embeddings.api_key:
            embeddings = embeddings.model_copy(
                update={"api_key": self.secret_manager.resolve_object(embeddings.api_key)})
        return provider.create(embeddings)

    def _build_stores(self) -> None:
        from nl2sql.auth import RBAC
        from nl2sql.execution import ExecutionStore
        from nl2sql.execution.artifacts import ArtifactStore, ArtifactStoreConfig
        from nl2sql.indexing.vector_store import VectorStore
        from nl2sql.schema.sqlite_store import SqliteSchemaStore

        config = self._config
        self.rbac = RBAC.from_policies(config.policies)
        self.stores.index.mkdir(parents=True, exist_ok=True)
        self.stores.state.parent.mkdir(parents=True, exist_ok=True)
        self.vector_store = VectorStore(collection_name=INDEX_COLLECTION,
                                        persist_directory=str(self.stores.index), embeddings=self.embedder)
        self.schema_store = SqliteSchemaStore(path=self.stores.state,
                                              max_versions=config.settings.schema_.max_versions)
        self.execution_store = ExecutionStore()
        self.artifact_store = ArtifactStore(ArtifactStoreConfig(base_uri=str(self.stores.results)))

    # ------------------------------------------------------------ reading

    @property
    def config(self) -> Config:
        """The current config. It changes only when a datasource is added in code."""
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
    def traces_dir(self) -> Path:
        return self.stores.traces

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

    # ------------------------------------------------------------ lifecycle

    def check_datasources(self) -> List[DatasourceStatus]:
        """Connects to each datasource; a failed one is kept out of routing until it connects again."""
        adapters = self.ds_registry.all_adapters()
        statuses = []
        for i, datasource in enumerate(self.config.datasources):
            adapter = adapters.get(datasource.id)
            try:
                reason = None if adapter.test_connection() else "The connection test failed."
            except Exception as e:
                reason = str(e) or type(e).__name__
            if reason is None:
                self.ds_registry.mark_available(datasource.id)
                statuses.append(DatasourceStatus(id=datasource.id, status="ready", required=datasource.required))
                continue
            self.ds_registry.mark_unavailable(datasource.id, reason)
            statuses.append(DatasourceStatus(
                id=datasource.id, status="failed", required=datasource.required, stage="connect",
                reason=reason,
                fix=f"Check datasources[{i}].connection for '{datasource.id}', and that the database "
                    "is reachable from this machine. The log has the driver's error."))
        return statuses

    def close(self) -> None:
        """Disposes the adapters' pools and closes the stores. Calling it again does nothing."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self.ds_registry.close()
        for store in (getattr(self, "schema_store", None), getattr(self, "vector_store", None)):
            close = getattr(store, "close", None)
            if callable(close):
                try:
                    close()
                except Exception as e:
                    logger.warning(f"Closing {type(store).__name__} failed: {e}")


def _llm_providers(config: Config):
    """``(path, provider name)`` for every provider the ``llm`` section names."""
    llm = config.llm
    if isinstance(llm.model, str):
        yield "llm.provider", llm.provider
    for agent, override in llm.agents.items():
        model = override.model if "model" in override.model_fields_set else llm.model
        provider = override.provider if "provider" in override.model_fields_set else llm.provider
        if isinstance(model, str) and provider and provider != llm.provider:
            yield f"llm.agents.{agent}.provider", provider


def _copy_into_global_settings(config: Config, stores: StorePaths) -> None:
    """Copy this engine's values into the process-global ``settings``.

    Only for the length of the migration (docs/architecture/configuration_v2.md,
    "Staging it"): the modules not yet moved onto the runtime read the global.
    The last migration pull request deletes the global and this function.
    """
    from nl2sql.common.settings import settings as target

    s = config.settings
    values: Dict[str, Any] = {
        "tenant_id": s.tenant_id,
        "global_timeout_sec": s.pipeline.timeout_s,
        "plan_cache_enabled": s.pipeline.plan_cache,
        "logical_validator_strict_columns": s.pipeline.strict_columns,
        "today": s.pipeline.today,
        "sql_agent_max_retries": s.pipeline.retries.max,
        "sql_agent_retry_base_delay_sec": s.pipeline.retries.base_delay_s,
        "sql_agent_retry_max_delay_sec": s.pipeline.retries.max_delay_s,
        "sql_agent_retry_jitter_sec": s.pipeline.retries.jitter_s,
        "schema_retrieval_full_snapshot_max_tables": s.retrieval.full_snapshot_max_tables,
        "result_row_limit": s.results.row_limit,
        "result_fetch_batch_size": s.results.fetch_batch_size,
        "answer_preview_rows": s.results.preview_rows,
        "trace_mode": s.traces.mode,
        "trace_sample_rows": s.traces.sample_rows,
        "trace_max_field_chars": s.traces.max_field_chars,
        "schema_store_max_versions": s.schema_.max_versions,
        "schema_version_mismatch_policy": s.schema_.version_mismatch,
        "curated_enabled": config.curated is not None,
        "curated_match_top_k": s.curated.match_top_k,
        "curated_match_floor": s.curated.match_floor,
        "feedback_enabled": s.feedback.enabled,
        "observability_exporter": s.observability.exporter,
        "otlp_endpoint": s.observability.otlp_endpoint,
        "rbac_refusal_names_tables": config.policies.refusal_names_tables,
        "llm_prices": {model: price.model_dump(exclude_none=True) for model, price in config.llm.prices.items()},
        "vector_store_path": str(stores.index),
        "schema_store_path": str(stores.state),
        "trace_dir": str(stores.traces),
        "result_artifact_base_uri": str(stores.results),
    }
    target.__dict__.update(values)
