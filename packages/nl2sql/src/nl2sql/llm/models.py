
"""The LLM settings ``LLMRegistry`` builds clients from: one ``AgentConfig`` per agent."""
from typing import Dict, Literal, Optional

from pydantic import BaseModel, Field, NonNegativeInt, PositiveFloat, PositiveInt, SecretStr, field_serializer


class AgentConfig(BaseModel):
    """One agent's resolved LLM settings, as ``LLMRegistry`` builds a client from them.

    ``agents_from_config`` makes one per agent from the ``llm`` section of the
    config (``nl2sql.config.LLM``).
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
    structured_output: Optional[Literal["tool_calling", "json_schema", "json_mode"]] = Field(
        None, description="How structured answers are asked for. None: the provider's default.")
    parse_retries: NonNegativeInt = Field(1, description="Retries of a malformed structured answer.")
    max_tokens: Optional[PositiveInt] = Field(None, description="None: the provider's default.")
    timeout_s: Optional[PositiveFloat] = Field(None, description="Per request. None: the client library's.")
    max_retries: Optional[NonNegativeInt] = Field(
        None, description="Network and rate-limit retries, done by the client. None: the client library's.")
    headers: Dict[str, SecretStr] = Field(
        default_factory=dict,
        description="Sent with every request. A value may be a ${provider:key} reference; none ever prints.")
    name: str = Field("default", description="Name of the agent")

    @field_serializer("api_key", when_used="json")
    def _serialize_api_key(self, value, info):
        """Masked in JSON: a log, a trace or an API response never gets the key."""
        return None if value is None else str(value)

    @field_serializer("headers", when_used="json")
    def _serialize_headers(self, value, info):
        """Masked in JSON, as the key is: only the header names show."""
        return {name: str(secret) for name, secret in value.items()}


class LLMFileConfig(BaseModel):
    """A set of agents to compare: ``default`` and the agents that differ from it.

    What the benchmark's tier 2 and matrix runs swap in, one config at a time.
    ``from_llm`` builds one from the ``llm`` section.
    """
    default: AgentConfig
    agents: Optional[Dict[str, AgentConfig]] = Field(default_factory=dict)

    @classmethod
    def from_llm(cls, llm) -> "LLMFileConfig":
        """The ``llm`` section (``nl2sql.config.LLM``) as one entry per agent."""
        from nl2sql.llm.registry import agents_from_config

        agents = agents_from_config(llm)
        return cls(default=agents.pop("default"), agents=agents)
