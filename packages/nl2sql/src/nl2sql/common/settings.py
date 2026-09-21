from typing import Dict, Literal, Optional
import os
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from nl2sql.common.logger import get_logger

logger = get_logger(__name__)

class Settings(BaseSettings):
    """Application configuration settings backed by environment variables."""
    
    openai_api_key: Optional[str] = Field(default=None, validation_alias="OPENAI_API_KEY")
    vector_store_path: Optional[str] = Field(
        default="./chroma_db",
        validation_alias="VECTOR_STORE",
        description="Persist directory for the vector store."
    )
    vector_store_collection_name: str = Field(
        default="nl2sql_store",
        validation_alias="VECTOR_STORE_COLLECTION",
        description="Chroma collection name for schema embeddings."
    )
    llm_config_path: str = Field(default="configs/llm.yaml", validation_alias="LLM_CONFIG")
    datasource_config_path: str = Field(default="configs/datasources.yaml", validation_alias="DATASOURCE_CONFIG")
    secrets_config_path: str = Field(default="configs/secrets.yaml", validation_alias="SECRETS_CONFIG")
    embedding_model: str = Field(default="text-embedding-3-small", validation_alias="EMBEDDING_MODEL")
    embedding_provider: str = Field(
        default="openai",
        validation_alias="EMBEDDING_PROVIDER",
        description="Embedding backend: 'openai' (API key required) or 'local' (key-free ONNX model)."
    )
    tenant_id: str = Field(default="default_tenant", validation_alias="TENANT_ID")
    sample_questions_path: str = Field(
        default="configs/sample_questions.yaml",
        # ROUTING_EXAMPLES is what `.env` files generated before the fix wrote
        # (and nothing read), so folders scaffolded then keep their questions.
        validation_alias=AliasChoices("SAMPLE_QUESTIONS", "ROUTING_EXAMPLES"),
        description="Path to the YAML file containing sample questions for routing."
    )
    policies_config_path: str = Field(
        default="configs/policies.json",
        validation_alias="POLICIES_CONFIG",
        description="Path to the JSON file containing RBAC policies and permissions."
    )
    
    global_timeout_sec: int = Field(
        default=60,
        validation_alias="GLOBAL_TIMEOUT_SEC",
        description="Global timeout in seconds for pipeline execution."
    )

    result_artifact_backend: str = Field(
        default="local",
        validation_alias="RESULT_ARTIFACT_BACKEND",
        description="Artifact backend to store executor results: local, s3, adls."
    )
    result_artifact_base_uri: str = Field(
        default="./artifacts",
        validation_alias="RESULT_ARTIFACT_BASE_URI",
        description="Base URI or path for artifact storage."
    )
    result_artifact_path_template: str = Field(
        default="<tenant_id>/<request_id>.parquet",
        validation_alias="RESULT_ARTIFACT_PATH_TEMPLATE",
        description=(
            "Template for artifact paths, relative to the backend root. "
            "Placeholders are <key> names resolved from executor metadata; "
            "the executor supplies tenant_id, request_id and schema_version."
        )
    )
    result_artifact_s3_bucket: Optional[str] = Field(
        default=None,
        validation_alias="RESULT_ARTIFACT_S3_BUCKET",
        description="S3 bucket for artifact storage."
    )
    result_artifact_s3_prefix: Optional[str] = Field(
        default=None,
        validation_alias="RESULT_ARTIFACT_S3_PREFIX",
        description="S3 prefix for artifact storage."
    )
    result_artifact_adls_account: Optional[str] = Field(
        default=None,
        validation_alias="RESULT_ARTIFACT_ADLS_ACCOUNT",
        description="ADLS storage account name."
    )
    result_artifact_adls_container: Optional[str] = Field(
        default=None,
        validation_alias="RESULT_ARTIFACT_ADLS_CONTAINER",
        description="ADLS container name."
    )
    result_artifact_adls_connection_string: Optional[str] = Field(
        default=None,
        validation_alias="RESULT_ARTIFACT_ADLS_CONNECTION_STRING",
        description="ADLS connection string, if using key-based auth."
    )

    schema_store_backend: str = Field(
        default="sqlite",
        validation_alias="SCHEMA_STORE_BACKEND",
        description="Schema store backend identifier (e.g., 'sqlite', 'memory')."
    )
    schema_store_path: str = Field(
        default="data/schema_store.db",
        validation_alias="SCHEMA_STORE_PATH",
        description="SQLite database path for schema store persistence."
    )
    schema_store_max_versions: int = Field(
        default=3,
        validation_alias="SCHEMA_STORE_MAX_VERSIONS",
        description="Max versions to retain per datasource in schema store."
    )
    schema_version_mismatch_policy: str = Field(
        default="warn",
        validation_alias="SCHEMA_VERSION_MISMATCH_POLICY",
        description="Action when chunk schema_version differs from SchemaStore: warn, fail, or ignore."
    )

    schema_retrieval_full_snapshot_max_tables: int = Field(
        default=15,
        validation_alias="SCHEMA_RETRIEVAL_FULL_SNAPSHOT_MAX_TABLES",
        description="Skip vector retrieval and pass the full schema snapshot when a datasource has at most this many tables."
    )

    logical_validator_strict_columns: bool = Field(
        default=False,
        validation_alias="LOGICAL_VALIDATOR_STRICT_COLUMNS",
        description="Treat missing columns as errors in logical validation."
    )

    rbac_refusal_names_tables: bool = Field(
        default=False,
        validation_alias="RBAC_REFUSAL_NAMES_TABLES",
        description=(
            "Name the forbidden table and the role in the refusal the user sees. Off by default: "
            "naming a table tells an unauthorised user that it exists. The table and role are "
            "always recorded in the error's details (and so the run trace) and the log. "
            "The generated demo turns it on, because showing the refusal is its point."
        ),
    )

    sql_agent_max_retries: int = Field(
        default=3,
        validation_alias="SQL_AGENT_MAX_RETRIES",
        description="Max retry attempts for SQL agent refinement loop."
    )
    sql_agent_retry_base_delay_sec: float = Field(
        default=1.0,
        validation_alias="SQL_AGENT_RETRY_BASE_DELAY_SEC",
        description="Base delay for SQL agent retries (seconds)."
    )
    sql_agent_retry_max_delay_sec: float = Field(
        default=10.0,
        validation_alias="SQL_AGENT_RETRY_MAX_DELAY_SEC",
        description="Max delay for SQL agent retries (seconds)."
    )
    sql_agent_retry_jitter_sec: float = Field(
        default=0.5,
        validation_alias="SQL_AGENT_RETRY_JITTER_SEC",
        description="Max jitter added to SQL agent retry delays (seconds)."
    )

    llm_prices: Dict[str, Dict[str, float]] = Field(
        default_factory=dict,
        validation_alias="LLM_PRICES",
        description=(
            "Optional per-model prices per million tokens, as JSON: "
            '{"gpt-4o": {"input": 2.5, "cached_input": 1.25, "output": 10}}. '
            "Without a price for a model, usage reports tokens only (cost is null)."
        ),
    )

    trace_mode: Literal["off", "on_failure", "always"] = Field(
        default="on_failure",
        validation_alias="TRACE_MODE",
        description=(
            "When to write a run trace (every node's inputs, outputs, LLM prompts and raw "
            "responses) to TRACE_DIR: 'off', 'on_failure' (the run returned errors, needed "
            "a retry, or did not complete) or 'always'. `nl2sql demo` writes 'always'."
        ),
    )
    trace_dir: str = Field(
        default="traces",
        validation_alias="TRACE_DIR",
        description="Directory run traces are written to, relative to the working directory.",
    )
    trace_sample_rows: int = Field(
        default=50,
        validation_alias="TRACE_SAMPLE_ROWS",
        description="Result rows kept in a trace; the rest are dropped with a truncation marker.",
    )
    trace_max_field_chars: int = Field(
        default=20000,
        validation_alias="TRACE_MAX_FIELD_CHARS",
        description=(
            "Longest string kept in a node's recorded inputs and outputs. LLM prompts and "
            "raw responses are never cut: replay compares them exactly."
        ),
    )

    observability_exporter: str = Field(
        default="none",
        validation_alias="OBSERVABILITY_EXPORTER",
        description="Exporter for metrics/traces: 'none', 'console', 'otlp'."
    )

    otlp_endpoint: Optional[str] = Field(
        default=None,
        validation_alias="OTEL_EXPORTER_OTLP_ENDPOINT",
        description="Endpoint for OTLP exporter (e.g. http://localhost:4317)."
    )

    model_config = SettingsConfigDict(
        env_file=".env", 
        env_file_encoding="utf-8", 
        extra="ignore"
    )


def load_settings() -> Settings:
    """Load settings using ENV_FILE_PATH or ENV/APP_ENV when provided."""
    env_file_path = os.getenv("ENV_FILE_PATH")
    env_name = os.getenv("ENV") or os.getenv("APP_ENV")

    if env_file_path:
        logger.info(f"Loading settings from ENV_FILE_PATH={env_file_path}")
        return Settings(_env_file=env_file_path)
    if env_name:
        env_file = f".env.{env_name}"
        logger.info(f"Loading settings from {env_file}")
        return Settings(_env_file=env_file)

    logger.info("Loading settings from default .env")
    return Settings()


settings = load_settings()

def reload_settings() -> Settings:
    """Re-read settings from the environment and refresh the module singleton.

    The ``settings`` object is created at import time, so switching environments
    at runtime requires updating it in place; existing
    ``from nl2sql.common.settings import settings`` references stay valid.
    """
    settings.__dict__.update(load_settings().__dict__)
    return settings
