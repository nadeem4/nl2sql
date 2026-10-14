"""The 0.4 config files, read into a ``Config``. Temporary: PR 7b deletes this module.

The CLI, ``nl2sql-api`` and the playground still start from the 0.4 files
(``configs/datasources.yaml``, ``llm.yaml``, ``policies.json``,
``secrets.yaml``, ``curated.yaml``, ``sample_questions.yaml`` and the
environment variables) until PR 7b moves them onto ``nl2sql.yaml``. Until then
``_legacy_config`` turns those files into the same ``Config`` an
``nl2sql.yaml`` would give, so there is one runtime, and ``legacy_runtime``
keeps their stores where the 0.4 variables put them. It is never released.
"""
from __future__ import annotations

import pathlib
from typing import Any, Dict, Optional, Union

from nl2sql.config import (
    LLM,
    AgentLLM,
    Config,
    Curated,
    CuratedSettings,
    Datasource,
    Embeddings,
    FeedbackSettings,
    Limits,
    ObservabilitySettings,
    PipelineSettings,
    Policies,
    Price,
    ResultSettings,
    RetrievalSettings,
    RetrySettings,
    Role,
    SchemaSettings,
    Secrets,
    Settings,
    Storage,
    TraceSettings,
)
from nl2sql.runtime import Runtime, StorePaths

PathLike = Optional[Union[str, pathlib.Path]]


def _secret(value: Any) -> Optional[str]:
    return value.get_secret_value() if hasattr(value, "get_secret_value") else value


def datasource_from_04(config: Any, sample_questions: Optional[list] = None) -> Datasource:
    """A 0.4 datasource entry (a ``DatasourceConfig`` or its dict) as a ``Datasource``."""
    from nl2sql.datasources.models import DatasourceConfig

    if isinstance(config, dict):
        config = DatasourceConfig(**config)
    connection = config.connection.model_dump()
    return Datasource(
        id=config.id,
        type=connection.pop("type").lower(),
        description=config.description,
        connection=connection,
        limits=Limits(row_limit=config.options.get("row_limit"),
                      timeout_ms=config.options.get("statement_timeout_ms"),
                      max_bytes=config.options.get("max_bytes")),
        preferred_tables=list(config.preferred_tables),
        sample_questions=list(sample_questions or []),
    )


def _llm(cfg: Any, prices: Dict[str, Dict[str, float]]) -> LLM:
    from nl2sql.llm.providers import LLM_AGENTS

    by_key = {key: agent for agent, key in LLM_AGENTS.items()}
    by_key["indexing_enrichment"] = "indexing_enrichment"

    def fields(agent: Any) -> Dict[str, Any]:
        return {"provider": agent.provider, "model": agent.model, "temperature": agent.temperature,
                "api_key": _secret(agent.api_key), "base_url": agent.base_url}

    agents = {by_key[key]: AgentLLM(**fields(agent)) for key, agent in (cfg.agents or {}).items() if key in by_key}
    return LLM(**fields(cfg.default), agents=agents,
               prices={model: Price(**price) for model, price in prices.items()})


def _settings(s: Any) -> Settings:
    return Settings(
        tenant_id=s.tenant_id,
        pipeline=PipelineSettings(
            timeout_s=max(1, int(s.global_timeout_sec)), plan_cache=s.plan_cache_enabled,
            strict_columns=s.logical_validator_strict_columns, today=s.today,
            retries=RetrySettings(max=s.sql_agent_max_retries, base_delay_s=s.sql_agent_retry_base_delay_sec,
                                  max_delay_s=s.sql_agent_retry_max_delay_sec,
                                  jitter_s=s.sql_agent_retry_jitter_sec)),
        retrieval=RetrievalSettings(full_snapshot_max_tables=s.schema_retrieval_full_snapshot_max_tables),
        results=ResultSettings(row_limit=s.result_row_limit, fetch_batch_size=s.result_fetch_batch_size,
                               preview_rows=s.answer_preview_rows),
        traces=TraceSettings(mode=s.trace_mode, sample_rows=s.trace_sample_rows,
                             max_field_chars=s.trace_max_field_chars),
        schema=SchemaSettings(max_versions=s.schema_store_max_versions,
                              version_mismatch=s.schema_version_mismatch_policy),
        curated=CuratedSettings(match_top_k=s.curated_match_top_k, match_floor=s.curated_match_floor),
        feedback=FeedbackSettings(enabled=s.feedback_enabled),
        observability=ObservabilitySettings(exporter=s.observability_exporter, otlp_endpoint=s.otlp_endpoint),
    )


def _vector_store_path(vector_store_path: PathLike) -> pathlib.Path:
    from nl2sql.common.settings import settings

    path = vector_store_path or settings.vector_store_path
    if not path:
        raise ValueError("VECTOR_STORE path is not configured. "
                         "Pass vector_store_path or set vector_store_path in settings.")
    return pathlib.Path(path)


def _legacy_config(
    ds_config_path: PathLike = None,
    secrets_config_path: PathLike = None,
    llm_config_path: PathLike = None,
    vector_store_path: PathLike = None,
    policies_config_path: PathLike = None,
) -> Config:
    """The ``Config`` the 0.4 files and environment variables describe.

    Each path defaults to its 0.4 variable, as the 0.4 constructor did.
    """
    from nl2sql.common.settings import secret_value, settings
    from nl2sql.configs import ConfigManager

    ds_path = pathlib.Path(ds_config_path or settings.datasource_config_path)
    secrets_path = pathlib.Path(secrets_config_path or settings.secrets_config_path)
    llm_path = pathlib.Path(llm_config_path or settings.llm_config_path)
    policies_path = pathlib.Path(policies_config_path or settings.policies_config_path)
    cm = ConfigManager(ds_config_path=ds_path, llm_config_path=llm_path,
                       policies_config_path=policies_path, secrets_config_path=secrets_path)

    datasources = [datasource_from_04(d, cm.get_example_questions(d.id)) for d in cm.load_datasources(ds_path)]
    policies = cm.load_policies(policies_path)
    curated = cm.load_curated() if settings.curated_enabled else None
    provider = (settings.embedding_provider or "openai").strip().lower()
    openai_key = secret_value(settings.openai_api_key)
    embeddings = Embeddings(
        provider=provider,
        model=settings.embedding_model if provider == "openai" else None,
        api_key=openai_key if provider == "openai" and openai_key else None,
    )
    return Config(
        datasources=datasources,
        llm=_llm(cm.load_llm(llm_path), settings.llm_prices),
        embeddings=embeddings,
        policies=Policies(
            roles={name: Role(description=role.description, allowed_datasources=role.allowed_datasources,
                              allowed_tables=role.allowed_tables) for name, role in policies.roles.items()},
            refusal_names_tables=settings.rbac_refusal_names_tables,
        ),
        storage=Storage(path=_vector_store_path(vector_store_path)),
        curated=Curated(datasources=curated.datasources) if curated is not None else None,
        secrets=Secrets(providers=[p for p in cm.load_secrets(secrets_path) if p.type != "env"]),
        settings=_settings(settings),
    )


def legacy_runtime(
    ds_config_path: PathLike = None,
    secrets_config_path: PathLike = None,
    llm_config_path: PathLike = None,
    vector_store_path: PathLike = None,
    policies_config_path: PathLike = None,
) -> Runtime:
    """The runtime for the 0.4 files, with its stores where the 0.4 variables put them."""
    from nl2sql.common.settings import settings

    config = _legacy_config(ds_config_path, secrets_config_path, llm_config_path, vector_store_path,
                            policies_config_path)
    stores = StorePaths(index=_vector_store_path(vector_store_path),
                        state=pathlib.Path(settings.schema_store_path),
                        traces=pathlib.Path(settings.trace_dir),
                        results=pathlib.Path(settings.result_artifact_base_uri))
    return Runtime.from_config(config, stores=stores)
