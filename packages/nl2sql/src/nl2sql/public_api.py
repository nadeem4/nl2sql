"""
Public API for NL2SQL Core Package

This module provides a clean, stable public interface to the NL2SQL core functionality.
It defines the official API boundaries and ensures backward compatibility.
"""

from __future__ import annotations

import os
import pathlib
from typing import TYPE_CHECKING, Optional, Union

if TYPE_CHECKING:
    from nl2sql.check import CheckReport
    from nl2sql.config import LLM, Config
    from nl2sql.registry import Registry
    from nl2sql.runtime import Runtime


class NL2SQL:
    """The engine: built from one ``Config``, and the one thing an application talks to.

    ``NL2SQL(config)``, ``NL2SQL.from_config(path)`` and ``NL2SQL.demo()``
    build it. Building it never connects to a datasource or calls a model;
    ``check()`` does, and ``close()`` releases what it opened. See
    docs/architecture/configuration_v2.md, section 4.
    """

    def __init__(self, config: "Config", *, registry: Optional["Registry"] = None):
        """Load and validate everything ``config`` names. Never connects.

        Resolves every name through ``registry`` (``Registry.default()`` when
        None), imports only the components the config uses, and builds the
        secret manager, the adapters (not connected), the LLM clients (no
        model call), the embedder and the stores under ``storage.path``.

        Raises:
            ConfigError: one error listing every problem found.
        """
        # The runtime is imported here, not at the top of the module, so
        # ``import nl2sql`` stays light (docs/architecture/invariants.md).
        from nl2sql.runtime import Runtime

        self._attach(Runtime.from_config(config, registry))

    @classmethod
    def from_config(cls, path: Optional[Union[str, os.PathLike]] = None, *,
                    registry: Optional["Registry"] = None) -> "NL2SQL":
        """``NL2SQL(load_config(path), registry=registry)``.

        ``path`` defaults to ``$NL2SQL_CONFIG``, then ``./nl2sql.yaml``.
        """
        from nl2sql.config import load_config

        return cls(load_config(path), registry=registry)

    @classmethod
    def demo(cls, *, llm: Optional["LLM"] = None,
             path: Union[str, os.PathLike] = ".nl2sql-demo") -> "NL2SQL":
        """The demo: three bundled SQLite databases, opened read-only from the installed package.

        With the demo's roles (admin, analyst, viewer) and verified queries,
        and its data under ``path``. Without ``llm``, the first provider
        whose key variable is set is used, with its default model.

        Raises:
            ConfigError: no ``llm`` was given and no provider key is set.
        """
        from nl2sql.config import demo_config

        return cls(demo_config(llm=llm if llm is not None else _demo_llm(), path=pathlib.Path(path)))

    @classmethod
    def _legacy(cls, **paths) -> "NL2SQL":
        """The engine from the 0.4 config files. For the CLI, ``nl2sql-api`` and the playground
        only, until they move onto ``nl2sql.yaml`` (PR 7b deletes it)."""
        from nl2sql._legacy import legacy_runtime

        engine = cls.__new__(cls)
        engine._attach(legacy_runtime(**paths))
        return engine

    def _attach(self, runtime: "Runtime") -> None:
        from nl2sql.api.query_api import QueryAPI
        from nl2sql.api.datasource_api import DatasourceAPI
        from nl2sql.api.llm_api import LLM_API
        from nl2sql.api.indexing_api import IndexingAPI
        from nl2sql.api.auth_api import AuthAPI
        from nl2sql.api.settings_api import SettingsAPI
        from nl2sql.api.policy_api import PolicyAPI
        from nl2sql.api.curated_api import CuratedAPI

        self._runtime = runtime
        self.query = QueryAPI(runtime)
        self.datasource = DatasourceAPI(runtime)
        self.llm = LLM_API(runtime)
        self.indexing = IndexingAPI(runtime)
        self.auth = AuthAPI(runtime)
        self.settings = SettingsAPI(runtime)
        self.policy = PolicyAPI(runtime)
        self.curated = CuratedAPI(runtime)
        self._benchmark = None

    # ------------------------------------------------------------ lifecycle

    @property
    def config(self) -> "Config":
        """The current frozen ``Config``. Secrets appear as references only."""
        return self._runtime.config

    def check(self) -> "CheckReport":
        """Connect to each datasource, check every agent's API key resolves, and report the index.

        A datasource that fails is marked unavailable and left out of routing;
        the engine runs degraded. No model is called. Safe to call again.

        Raises:
            EngineNotReady: a ``required`` datasource failed, or datasources
                are configured and none is ready.
        """
        from nl2sql.check import CheckReport, EngineNotReady

        self._ensure_open()
        statuses = self._runtime.check_datasources()
        report = CheckReport(datasources=statuses, llm=self._runtime.llm_registry.missing_keys(),
                             index=self.index_health()["status"])
        required = [d.id for d in statuses if d.required and d.status == "failed"]
        if required:
            raise EngineNotReady(f"The required datasource(s) {', '.join(required)} failed.", report)
        if statuses and not any(d.status == "ready" for d in statuses):
            raise EngineNotReady("No datasource is ready.", report)
        return report

    def close(self) -> None:
        """Dispose the adapters' pools and close the stores. The engine cannot be used afterwards.

        Calling it twice is fine.
        """
        self._runtime.close()

    @property
    def closed(self) -> bool:
        return self._runtime.closed

    def __enter__(self) -> "NL2SQL":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def _ensure_open(self) -> None:
        runtime = getattr(self, "_runtime", None)
        if runtime is not None and runtime.closed:
            raise RuntimeError("This engine is closed. Build a new one.")

    @property
    def benchmark(self):
        """The benchmark API, built on first use so ``import nl2sql`` never loads ``nl2sql.evaluation``."""
        if self._benchmark is None:
            from nl2sql.api.benchmark_api import BenchmarkAPI

            self._benchmark = BenchmarkAPI(self._runtime)
        return self._benchmark

    @property
    def context(self) -> "Runtime":
        """The runtime (internal use only; removed with the facade namespaces)."""
        return self._runtime

    # Convenience methods that delegate to the modular APIs
    def run_query(
        self,
        natural_language: str,
        datasource_id: Optional[str] = None,
        execute: bool = True,
        user_context=None,
        cancellation_token=None,
    ):
        """
        Execute a natural language query against the database.

        Pass a :class:`~nl2sql.CancellationToken` and call its ``cancel()``
        from another thread to stop the run before its next step.
        """
        self._ensure_open()
        return self.query.run_query(
            natural_language=natural_language,
            datasource_id=datasource_id,
            execute=execute,
            user_context=user_context,
            cancellation_token=cancellation_token,
        )

    def stream_query(
        self,
        natural_language: str,
        datasource_id: Optional[str] = None,
        execute: bool = True,
        user_context=None,
        cancellation_token=None,
    ):
        """
        Run a question as ``run_query`` does, yielding :class:`~nl2sql.QueryEvent`
        values as it goes: ``node_started`` / ``node_finished`` per step,
        ``result_manifest`` when the results are saved, ``answer_delta`` as the
        answer is written, then ``final`` (``run_query``'s result, as JSON) or
        ``error``. ``event.sse()`` is the event as one Server-Sent Event.
        Closing the iterator early cancels the run.
        """
        self._ensure_open()
        return self.query.stream_query(
            natural_language=natural_language,
            datasource_id=datasource_id,
            execute=execute,
            user_context=user_context,
            cancellation_token=cancellation_token,
        )

    def get_result_manifest(self, request_id: str, tenant_id: Optional[str] = None):
        """
        Which results a run saved (node id, columns, row count, truncated), by
        its ``trace_id``. Raises ``ResultNotFound`` for an unknown run.
        """
        return self.query.get_result_manifest(request_id, tenant_id=tenant_id)

    def get_result_page(self, request_id: str, result_id: str, offset: int = 0, limit: int = 100,
                        tenant_id: Optional[str] = None):
        """
        One page of a run's saved result, read lazily from its parquet file.
        ``limit`` is capped at 1,000. Raises ``ResultNotFound`` for an unknown
        run or result, ``ValueError`` for a negative offset or a limit below 1.
        """
        return self.query.get_result_page(request_id, result_id, offset=offset, limit=limit,
                                          tenant_id=tenant_id)

    def export_result_csv(self, request_id: str, result_id: str, tenant_id: Optional[str] = None):
        """
        The whole of a run's saved result as CSV: an iterator of UTF-8 chunks,
        header first. Raises ``ResultNotFound`` before streaming for an unknown
        run or result.
        """
        return self.query.export_result_csv(request_id, result_id, tenant_id=tenant_id)

    def add_datasource(self, config):
        """
        Add a ``Datasource`` to the engine. Its adapter is built but not
        connected, and it joins ``engine.config``, description and sample
        questions included.
        """
        return self.datasource.add_datasource(config)

    def add_datasource_from_config(self, config_path: Union[str, pathlib.Path]):
        """
        Add datasources from a configuration file.
        """
        return self.datasource.add_datasource_from_config(config_path)

    def list_datasources(self) -> list:
        """
        List all registered datasource IDs.
        """
        return self.datasource.list_datasources()

    def list_unavailable_datasources(self) -> dict:
        """
        Configured datasources that failed to start, mapped to the reason.

        They are left out of ``list_datasources``; a query or an index run
        that names one fails with this reason.
        """
        unavailable = getattr(self._runtime.ds_registry, "unavailable", None)
        return unavailable() if unavailable else {}

    def get_datasource_capabilities(self, datasource_id: str) -> dict:
        """
        Get capabilities of a specific datasource.
        """
        return self.datasource.get_capabilities(datasource_id)


    def configure_llm(self, config):
        """
        Programmatically configure an LLM.
        """
        return self.llm.configure_llm(config)

    def configure_llm_from_config(self, config_path: Union[str, pathlib.Path]):
        """
        Configure LLMs from a configuration file.
        """
        return self.llm.configure_llm_from_config(config_path)


    def list_llms(self) -> dict:
        """
        List all configured LLMs.
        """
        return self.llm.list_llms()
    
    def get_llm(self, llm_name: str) -> dict:
        """
        Get details of a specific LLM.
        """
        return self.llm.get_llm(llm_name)
    
    def index_datasource(self, datasource_id: str, enrich: bool = False):
        """
        Index schema for a specific datasource.

        ``enrich`` asks the ``indexing_enrichment`` LLM for descriptions and
        spends tokens, so it is off unless asked for.
        """
        return self.indexing.index_datasource(datasource_id, enrich=enrich)

    def index_all_datasources(self, enrich: bool = False):
        """
        Index schema for all registered datasources.

        ``enrich`` asks the ``indexing_enrichment`` LLM for descriptions and
        spends tokens, so it is off unless asked for.
        """
        return self.indexing.index_all_datasources(enrich=enrich)

    def clear_index(self):
        """
        Clear the vector store index.
        """
        return self.indexing.clear_index()

    # Auth API convenience methods
    def check_permissions(self, user_context, datasource_id, table, schema=None):
        """
        Check if a user has permission to access a specific resource.

        ``schema`` defaults to the datasource's default schema.
        """
        return self.auth.check_permissions(user_context, datasource_id, table, schema)

    def get_allowed_resources(self, user_context):
        """
        Get resources a user has access to.
        """
        return self.auth.get_allowed_resources(user_context)

    def get_current_settings(self):
        """
        Get the current application settings.
        """
        return self.settings.get_current_settings()

    def get_setting(self, key):
        """
        Get a specific setting value.
        """
        return self.settings.get_setting(key)

    def validate_configuration(self):
        """
        Validate the current configuration.
        """
        return self.settings.validate_configuration()

    # Schema, index and retrieval: what the playground and nl2sql-api show.
    # Imports are local so ``import nl2sql`` stays light.

    def get_schema(self, datasource_id: str) -> dict:
        """
        The datasource's indexed schema, exactly as the planner is given it.

        Tables sorted by name, each with its columns (type, nullable, primary
        key, description), foreign keys, row count and description. Read from
        the latest schema snapshot, not the database; before the first index
        ``tables`` is empty.
        """
        from nl2sql.schema.view import schema_view

        store = self._runtime.schema_store
        snapshot = store.get_latest_snapshot(datasource_id) if store is not None else None
        return schema_view(datasource_id, snapshot)

    def index_health(self) -> dict:
        """
        Health of the vector index: ``status`` (``ok``, ``empty``, ``stale`` or
        ``missing``), entry counts by type, when it was built, the embedding
        model, one entry per registered datasource, any problems found, and
        ``warnings`` that leave ``status`` alone (a ``curated.yaml`` changed
        since it was indexed, a preferred table missing from the indexed
        schema, a datasource that failed to start).
        """
        from nl2sql.indexing.curated import curated_hashes
        from nl2sql.indexing.health import IndexHealth, inspect_vector_store

        registry = self._runtime.ds_registry
        unavailable = getattr(registry, "unavailable", None)
        down = [f"Datasource '{ds}' is unavailable: {reason}"
                for ds, reason in (unavailable() if unavailable else {}).items()]
        store = self._runtime.vector_store
        if store is None:
            health = IndexHealth(status="missing", problems=["No vector index is configured."])
        else:
            ids = registry.list_ids() if registry is not None else []
            hashes = curated_hashes(getattr(self._runtime, "curated", None), ids)
            preferred_of = getattr(registry, "get_preferred_tables", None)
            preferred = {ds: preferred_of(ds) for ds in ids} if preferred_of else None
            health = inspect_vector_store(store, self._runtime.schema_store, ids, curated_hashes=hashes,
                                          preferred_tables=preferred)
        health.warnings.extend(down)
        return health.to_dict()

    def rebuild_index(self, datasource_ids=None, enrich: bool = False, full: bool = False,
                      on_progress=None, switch_guard=None):
        """
        Rebuild the vector index beside the live one, then switch to it.

        The current entries keep answering questions until the new ones are
        complete. ``enrich`` asks the LLM for descriptions and spends tokens,
        so it is off unless asked for. ``on_progress`` is called with a
        sentence before each step; ``switch_guard`` is a context manager
        factory held around each switch. Returns a ``RebuildResult``
        (``ok``, ``stats``, ``empty``, ``errors``).
        """
        from nl2sql.indexing import rebuild

        return rebuild.rebuild_index(
            self._runtime,
            enrich=enrich,
            datasource_ids=datasource_ids,
            full=full,
            on_progress=on_progress,
            switch_guard=switch_guard,
        )

    def inspect_retrieval(self, query: str, k: int = 8, lambda_mult: Optional[float] = None,
                          types=None, datasource_id: Optional[str] = None) -> dict:
        """
        One MMR search of the live index, as the engine runs it: the candidate
        pool with scores, the picks in order and what was dropped. ``types``
        limits the entry types (``schema.table`` ...). It reads index metadata
        only and needs no LLM.

        Raises:
            LookupError: when no vector index is configured.
        """
        store = self._runtime.vector_store
        if store is None:
            raise LookupError("There is no vector index configured.")
        kwargs = {"lambda_mult": lambda_mult} if lambda_mult is not None else {}
        return store.inspect(query, k=k, types=types, datasource_id=datasource_id, **kwargs)

    def reload_llm_config(self, config_path: Union[str, pathlib.Path]) -> None:
        """
        Replace every configured LLM with those in ``config_path``, as one step.

        Unlike ``configure_llm_from_config`` this also forgets agents the file
        no longer names, so they fall back to ``default``. An invalid file
        leaves the current configuration in place.
        """
        from nl2sql.configs import ConfigManager

        cfg = ConfigManager().load_llm(pathlib.Path(config_path))
        agents = dict(cfg.agents or {})
        agents["default"] = cfg.default
        self._runtime.llm_registry.replace_llms(agents)


def _demo_llm() -> "LLM":
    """The demo's LLM when none is given: the first provider whose key variable is set."""
    from nl2sql.config import ConfigError, Problem, default_llm
    from nl2sql.llm.providers import DEFAULT_MODELS, KEYED_PROVIDERS, api_key_from_env, env_var_for_provider

    providers = [name for name in KEYED_PROVIDERS if name in DEFAULT_MODELS]
    for provider in providers:
        if api_key_from_env(provider):
            return default_llm(provider)
    variables = ", ".join(env_var_for_provider(p) for p in providers)
    raise ConfigError([Problem(
        file="NL2SQL.demo()", path="llm", message=f"No LLM was given and none of {variables} is set.",
        fix="Set one of them, or pass llm=LLM(provider=..., model=...).")], source="NL2SQL.demo()")
