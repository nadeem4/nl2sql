
from typing import Optional, Dict
from pydantic import BaseModel, Field, SecretStr, field_serializer

class AgentConfig(BaseModel):
    """Configuration for a specific agent's LLM.

    NOTE: this model is duplicated in ``nl2sql.llm.models``, which is what
    ``LLMRegistry`` consumes. Keep the two in sync until they are unified.
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

class LLMFileConfig(BaseModel):
    """Global LLM configuration (File Envelope)."""
    version: int = Field(1, description="Schema version")
    default: AgentConfig
    agents: Optional[Dict[str, AgentConfig]] = Field(default_factory=dict)
