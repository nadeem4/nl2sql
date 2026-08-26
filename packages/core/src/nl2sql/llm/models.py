

from pydantic import BaseModel, Field
from typing import Optional
from pydantic import SecretStr, field_serializer

class AgentConfig(BaseModel):
    """Configuration for a specific agent's LLM.

    NOTE: this model is duplicated in ``nl2sql.configs.llm``, which is what
    ``ConfigManager``/``LLMGenerator`` read and write. Keep the two in sync
    until they are unified.
    """
    provider: str
    model: str
    temperature: float = 0.0
    api_key: Optional[SecretStr] = None
    base_url: Optional[str] = Field(
        None,
        description=(
            "Override the provider endpoint. Defaults to the provider preset: "
            "the OpenRouter gateway for 'openrouter', the local Ollama daemon "
            "for 'ollama', the client default for 'openai'. Set it to reach any "
            "other OpenAI-compatible endpoint."
        ),
    )
    name: str = Field("default", description="Name of the agent")

    @field_serializer("api_key", when_used="json")
    def _serialize_api_key(self, value):
        return value.get_secret_value() if value else None
