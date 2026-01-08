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
    users_config_path: str = Field(
        default="users.json",
        validation_alias="USERS_CONFIG",
        description="Path to the JSON file containing user profiles and permissions."
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

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
