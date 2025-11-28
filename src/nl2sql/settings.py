from typing import Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    openai_api_key: Optional[str] = Field(default=None, validation_alias="OPENAI_API_KEY")
    vector_store_path: Optional[str] = Field(default="./chroma_db", validation_alias="VECTOR_STORE")
    llm_config_path: str = Field(default="configs/llm.example.yaml", validation_alias="LLM_CONFIG")
    datasource_config_path: str = Field(default="configs/datasources.example.yaml", validation_alias="DATASOURCE_CONFIG")

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

settings = Settings()
