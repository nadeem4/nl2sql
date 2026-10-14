from datetime import date, datetime, timezone
from typing import Any, Dict, Literal, Optional
import os
from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from nl2sql.common.logger import get_logger

logger = get_logger(__name__)


def secret_value(value: Any) -> Optional[str]:
    """The plain value of a ``SecretStr`` setting, for the one place that uses it.

    Secrets are held as ``SecretStr`` so ``repr``, ``str``, logs and tracebacks
    show a mask. Call this only where the value is actually needed. A plain
    string (e.g. a test's monkeypatch) passes through unchanged.
    """
    if isinstance(value, SecretStr):
        return value.get_secret_value()
    return value


CURATED_CONFIG_DEFAULT = "configs/curated.yaml"


class Settings(BaseSettings):
    """Application configuration settings backed by environment variables.

    Credentials are ``SecretStr``: read them with :func:`secret_value` at the
    point of use, never by printing the settings object.
    """

    openai_api_key: Optional[SecretStr] = Field(default=None, validation_alias="OPENAI_API_KEY")
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
    curated_config_path: str = Field(
        default=CURATED_CONFIG_DEFAULT,
        validation_alias="CURATED_CONFIG",
        description=(
            "Path to the curated answers file (verified queries). Absent at the default path: "
            "the feature is off. Set to any other path that does not exist: startup fails."
        ),
    )
    curated_enabled: bool = Field(
        default=True,
        validation_alias="CURATED_ENABLED",
        description=(
            "Load the curated answers file at startup. False turns the feature off without "
            "removing the file; `nl2sql curated validate` still checks it."
        ),
    )
    curated_match_top_k: int = Field(
        default=5,
        ge=1,
        validation_alias="CURATED_MATCH_TOP_K",
        description="Verified query candidates a question's search returns, one per query.",
    )
    curated_match_floor: float = Field(
        default=0.6,
        ge=0.0,
        le=1.0,
        validation_alias="CURATED_MATCH_FLOOR",
        description=(
            "Cosine similarity the best verified query candidate must reach before the model is asked "
            "to choose one. Calibrated on the local embedding model."
        ),
    )

    global_timeout_sec: int = Field(
        default=60,
        validation_alias="GLOBAL_TIMEOUT_SEC",
        description="Global timeout in seconds for pipeline execution."
    )

    result_artifact_base_uri: str = Field(
        default="./artifacts",
        validation_alias="RESULT_ARTIFACT_BASE_URI",
        description="Base URI or path for artifact storage."
    )
    result_artifact_path_template: str = Field(
        default="<tenant_id>/<request_id>/<sub_query_id>.parquet",
        validation_alias="RESULT_ARTIFACT_PATH_TEMPLATE",
        description=(
            "Template for artifact paths, relative to the backend root. "
            "Placeholders are <key> names resolved from executor metadata; "
            "the executor supplies tenant_id, request_id, sub_query_id, "
            "dag_node_id, subgraph_name and schema_version. A template naming "
            "neither <sub_query_id> nor <dag_node_id> gets -<sub_query_id> "
            "added to its file name, so the sub-queries of a run never share one."
        )
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

    plan_cache_enabled: bool = Field(
        default=True,
        validation_alias="PLAN_CACHE_ENABLED",
        description=(
            "Reuse a plan that already passed validation and executed, for the same "
            "sub-query intent, datasource and schema version, instead of calling the planner "
            "LLM. The cached plan is validated again on every use. Stored in the schema store; "
            "`nl2sql cache clear` empties it."
        ),
    )

    feedback_enabled: bool = Field(
        default=True,
        validation_alias="FEEDBACK_ENABLED",
        description=(
            "Let the playground record a thumbs up or down, with an optional note, for a run. "
            "Stored in the schema store's `feedback` table: the question, role, status, SQL, error "
            "codes and the model per node, never result rows or sample values. Local only, like "
            "Settings. `nl2sql feedback clear` empties it."
        ),
    )

    schema_retrieval_full_snapshot_max_tables: int = Field(
        default=15,
        validation_alias="SCHEMA_RETRIEVAL_FULL_SNAPSHOT_MAX_TABLES",
        description="Skip vector retrieval and pass the full schema snapshot when a datasource has at most this many tables."
    )

    logical_validator_strict_columns: bool = Field(
        default=True,
        validation_alias="LOGICAL_VALIDATOR_STRICT_COLUMNS",
        description=(
            "Treat a column the plan names but the schema does not have as a blocking, "
            "retryable error. Off, it is a warning and the plan can reach the database."
        ),
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
            "Optional 'cache_write' prices cache writes; it defaults to input x 1.25 on "
            "Claude and input on the OpenAI wire. "
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
    result_row_limit: int = Field(
        default=100_000,
        validation_alias="RESULT_ROW_LIMIT",
        description=(
            "The most rows one sub-query returns: a resource ceiling. A datasource's own "
            "row_limit applies when it is lower. A result that reaches it is flagged as "
            "truncated."
        ),
    )
    result_fetch_batch_size: int = Field(
        default=10_000,
        ge=1,
        validation_alias="RESULT_FETCH_BATCH_SIZE",
        description=(
            "Rows fetched from the database and written to the result artifact at a time, "
            "for an adapter that streams (execute_batches). At most one batch is held in memory."
        ),
    )
    answer_preview_rows: int = Field(
        default=20,
        validation_alias="ANSWER_PREVIEW_ROWS",
        description=(
            "Result rows the answer model sees, with the total row count and the column "
            "names. The full result is shown as a table, not written out by the model."
        ),
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

    today: Optional[date] = Field(
        default=None,
        validation_alias="NL2SQL_TODAY",
        description=(
            "The date the planner and refiner are told it is, as an ISO date (2013-12-31). "
            "Unset, it is the real date in UTC. Pin it for tests and gold runs whose "
            "questions say 'this year' or 'the last 30 days'."
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

def run_date() -> date:
    """Today, for the prompts: ``NL2SQL_TODAY`` when set, else the UTC date."""
    return settings.today or datetime.now(timezone.utc).date()


def reload_settings() -> Settings:
    """Re-read settings from the environment and refresh the module singleton.

    The ``settings`` object is created at import time, so switching environments
    at runtime requires updating it in place; existing
    ``from nl2sql.common.settings import settings`` references stay valid.
    """
    settings.__dict__.update(load_settings().__dict__)
    return settings
