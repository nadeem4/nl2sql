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
    sample_questions_path: str = Field(
        default="configs/sample_questions.yaml", 
        validation_alias="ROUTING_EXAMPLES",
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
