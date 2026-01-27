from typing import Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from dotenv import load_dotenv

# Load environment variables from .env into os.environ
load_dotenv()


class Settings(BaseSettings):
    """Application configuration settings backed by environment variables."""
    
    openai_api_key: Optional[str] = Field(default=None, validation_alias="OPENAI_API_KEY")
    vector_store_path: Optional[str] = Field(default="./chroma_db", validation_alias="VECTOR_STORE")
    llm_config_path: str = Field(default="configs/llm.yaml", validation_alias="LLM_CONFIG")
    datasource_config_path: str = Field(default="configs/datasources.yaml", validation_alias="DATASOURCE_CONFIG")
    benchmark_config_path: str = Field(default="configs/benchmark_suite.yaml", validation_alias="BENCHMARK_CONFIG")
    secrets_config_path: str = Field(default="configs/secrets.yaml", validation_alias="SECRETS_CONFIG")
    embedding_model: str = Field(default="text-embedding-3-small", validation_alias="EMBEDDING_MODEL")
    tenant_id: str = Field(default="default_tenant", validation_alias="TENANT_ID")
    sample_questions_path: str = Field(
        default="configs/sample_questions.yaml", 
        validation_alias="SAMPLE_QUESTIONS",
        description="Path to the YAML file containing sample questions for routing."
    )
    policies_config_path: str = Field(
        default="configs/policies.json",
        validation_alias="POLICIES_CONFIG",
        description="Path to the JSON file containing RBAC policies and permissions."
    )
    
    router_l1_threshold: float = Field(
        default=0.4, 
        validation_alias="ROUTER_L1_THRESHOLD",
        description="Distance threshold for Layer 1 (Vector Search) to be considered a match."
    )
    router_l2_threshold: float = Field(
        default=0.6, 
        validation_alias="ROUTER_L2_THRESHOLD", 
        description="relaxed distance threshold for Layer 2 (Multi-Query) voting."
    )
    
    global_timeout_sec: int = Field(
        default=60,
        validation_alias="GLOBAL_TIMEOUT_SEC",
        description="Global timeout in seconds for pipeline execution."
    )

    sandbox_exec_workers: int = Field(
        default=4,
        validation_alias="SANDBOX_EXEC_WORKERS",
        description="Max workers for latency-sensitive execution pool."
    )

    sandbox_index_workers: int = Field(
        default=2,
        validation_alias="SANDBOX_INDEX_WORKERS",
        description="Max workers for throughput-heavy indexing pool."
    )

    default_row_limit: int = Field(
        default=10000,
        validation_alias="DEFAULT_ROW_LIMIT",
        description="Default row limit for SQL execution safeguards."
    )
    default_max_bytes: int = Field(
        default=10485760, # 10 MB
        validation_alias="DEFAULT_MAX_BYTES",
        description="Default max bytes limit for SQL execution safeguards."
    )
    default_statement_timeout_ms: int = Field(
        default=30000, # 30s
        validation_alias="DEFAULT_STATEMENT_TIMEOUT_MS",
        description="Default statement timeout for SQL execution safeguards."
    )

    schema_store_backend: str = Field(
        default="memory",
        validation_alias="SCHEMA_STORE_BACKEND",
        description="Schema store backend identifier (e.g., 'memory', 'redis')."
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

    logical_validator_strict_columns: bool = Field(
        default=False,
        validation_alias="LOGICAL_VALIDATOR_STRICT_COLUMNS",
        description="Treat missing columns as errors in logical validation."
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

    audit_log_path: str = Field(
        default="logs/audit_events.log",
        validation_alias="AUDIT_LOG_PATH",
        description="Path to the persistent audit log file."
    )

    model_config = SettingsConfigDict(
        env_file=".env", 
        env_file_encoding="utf-8", 
        extra="ignore"
    )

    def configure_env(self, env: str) -> None:
        """Loads environment-specific variables and reloads settings."""
        if not env:
            return
            
        load_dotenv(f".env.{env}", override=True)
        new_settings = Settings()
        self.__dict__.update(new_settings.__dict__)

settings = Settings()

# Configure logging during import
from nl2sql.common.logger import configure_logging
configure_logging(
    level="INFO",
    json_format=(settings.observability_exporter == "otlp")
)
