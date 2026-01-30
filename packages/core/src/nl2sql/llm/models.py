

from pydantic import BaseModel, Field
from typing import Optional
from pydantic import SecretStr

class AgentConfig(BaseModel):
    """Configuration for a specific agent's LLM."""
    provider: str
    model: str
    temperature: float = 0.0
    api_key: Optional[SecretStr] = None
    name: str = Field("default", description="Name of the agent")