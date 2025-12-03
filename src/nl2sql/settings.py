from typing import Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    openai_api_key: Optional[str] = Field(default=None, validation_alias="OPENAI_API_KEY")
    vector_store_path: Optional[str] = Field(default="./chroma_db", validation_alias="VECTOR_STORE")
    llm_config_path: str = Field(default="configs/llm.yaml", validation_alias="LLM_CONFIG")
    datasource_config_path: str = Field(default="configs/datasources.yaml", validation_alias="DATASOURCE_CONFIG")
    benchmark_config_path: str = Field(default="configs/benchmark_suite.yaml", validation_alias="BENCHMARK_CONFIG")
    embedding_model: str = Field(default="text-embedding-3-small", validation_alias="EMBEDDING_MODEL")
    sample_questions_path: str = Field(
        default="configs/sample_questions.yaml", 
        validation_alias="ROUTING_EXAMPLES",
        description="Path to the YAML file containing sample questions for routing."
    )

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

settings = Settings()
