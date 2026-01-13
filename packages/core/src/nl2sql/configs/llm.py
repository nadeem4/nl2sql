
from typing import Optional, Dict
from pydantic import BaseModel, Field, SecretStr

class AgentConfig(BaseModel):
    """Configuration for a specific agent's LLM."""
    provider: str
    model: str
    temperature: float = 0.0
    api_key: Optional[SecretStr] = None
    base_url: Optional[str] = None

class LLMFileConfig(BaseModel):
    """Global LLM configuration (File Envelope)."""
    version: int = Field(1, description="Schema version")
    default: AgentConfig
    agents: Optional[Dict[str, AgentConfig]] = None
