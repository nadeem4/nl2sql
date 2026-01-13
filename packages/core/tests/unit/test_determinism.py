import pytest
from unittest.mock import MagicMock
from nl2sql.services.llm import LLMRegistry
from nl2sql.configs import LLMFileConfig, AgentConfig

def test_determinism_enforcement():
    """Verifies that temperature is 0.0 and seed is 42 regardless of config."""
    
    # Config with high temperature
    config = LLMFileConfig(
        default=AgentConfig(
            provider="openai",
            model="gpt-4o",
            temperature=0.9, # Should be ignored
            api_key="sk-test"
        )
    )
    
    registry = LLMRegistry(config)
    llm = registry._base_llm("test_agent")
    
    # Check enforcement
    assert llm.temperature == 0.0
    assert getattr(llm, "seed", None) == 42
