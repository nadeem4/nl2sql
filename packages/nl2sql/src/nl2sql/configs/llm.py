
from typing import Optional, Dict
from pydantic import BaseModel, Field, SecretStr, field_serializer

# Serialization context key that makes ``AgentConfig.api_key`` dump its real value.
REVEAL_SECRETS = "reveal_secrets"

class AgentConfig(BaseModel):
    """Configuration for a specific agent's LLM.

    The single definition. ``nl2sql.llm.models`` re-exports this class, so
    ``ConfigManager``/``LLMGenerator`` and ``LLMRegistry`` share one model.
    Do not add a second copy: the previous duplicate silently swallowed a fix
    applied to only one of the two.
    """
    provider: str
    model: str
    temperature: Optional[float] = Field(
        0.0,
        description=(
            "Sampling temperature sent with every call. Set it to null to send no "
            "temperature at all, for models that accept only their default: on "
            "2026-09-20 gpt-5.5 and gpt-5-mini rejected temperature=0 with HTTP 400."
        ),
    )
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
    def _serialize_api_key(self, value, info):
        """Masked in JSON unless a config writer asks for the real value.

        ``LLMGenerator`` writes ``llm.yaml`` with ``context={REVEAL_SECRETS: True}``
        so the file keeps the literal key or the ``${env:...}`` reference; any
        other JSON dump (a log, a trace, an API response) gets the mask.
        """
        if value is None:
            return None
        if (info.context or {}).get(REVEAL_SECRETS):
            return value.get_secret_value()
        return str(value)

class LLMFileConfig(BaseModel):
    """Global LLM configuration (File Envelope)."""
    version: int = Field(1, description="Schema version")
    default: AgentConfig
    agents: Optional[Dict[str, AgentConfig]] = Field(default_factory=dict)
