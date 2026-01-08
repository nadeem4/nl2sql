
import pathlib
import os
import pytest
from unittest.mock import MagicMock, patch
from nl2sql.services.llm import load_llm_config, LLMRegistry, LLMConfig
from nl2sql.common.settings import settings

@pytest.fixture
def mock_path():
    """Returns a MagicMock simulating a pathlib.Path object."""
    return MagicMock(spec=pathlib.Path)

def test_load_llm_config_success(mock_path):
    """Verifies that load_llm_config correctly parses a valid YAML file into an LLMConfig object."""
    mock_path.exists.return_value = True
    mock_path.read_text.return_value = """
    default:
      provider: "openai"
      model: "gpt-4o"
      temperature: 0.1
    agents:
      planner:
        temperature: 0.7
    """
    
    config = load_llm_config(mock_path)
    
    assert isinstance(config, LLMConfig)
    assert config.default.model == "gpt-4o"
    assert config.default.temperature == 0.1
    assert "planner" in config.agents

def test_llm_config_inheritance(mock_path):
    """
    Verifies that agents inherit configuration from the default block 
    and override specific fields correctly.
    """
    mock_path.exists.return_value = True
    mock_path.read_text.return_value = """
    default:
      provider: "openai"
      model: "gpt-4-turbo"
      temperature: 0.0
    agents:
      planner:
        temperature: 0.9
      creative:
        model: "gpt-4o"
    """
    
    config = load_llm_config(mock_path)
    
    # Planner: Inherits model, overrides temperature
    planner = config.agents["planner"]
    assert planner.model == "gpt-4-turbo"
    assert planner.temperature == 0.9
    
    # Creative: Inherits temperature, overrides model
    creative = config.agents["creative"]
    assert creative.model == "gpt-4o"
    assert creative.temperature == 0.0

def test_llm_registry_api_key_resolution_config(mock_path):
    """Verifies that LLMRegistry uses the API key from the config if present."""
    mock_path.read_text.return_value = """
    default:
      provider: "openai"
      model: "gpt-4"
      api_key: "sk-config-key"
    """
    mock_path.exists.return_value = True
    config = load_llm_config(mock_path)
    registry = LLMRegistry(config)
    
    # We inspect the private method or resulting object to verify key
    # _base_llm returns ChatOpenAI
    llm = registry._base_llm("default")
    assert llm.openai_api_key.get_secret_value() == "sk-config-key"

def test_llm_registry_api_key_resolution_env(mock_path):
    """Verifies that LLMRegistry falls back to os.environ/settings if config key is missing."""
    mock_path.read_text.return_value = """
    default:
      provider: "openai"
      model: "gpt-4"
    """
    mock_path.exists.return_value = True
    config = load_llm_config(mock_path)
    registry = LLMRegistry(config)
    
    # Patch settings.openai_api_key
    with patch.object(settings, 'openai_api_key', "sk-env-key"):
        llm = registry._base_llm("default")
        assert llm.openai_api_key.get_secret_value() == "sk-env-key"

def test_llm_registry_missing_api_key_error(mock_path):
    """Verifies that LLMRegistry raises RuntimeError if no API key is found anywhere."""
    mock_path.read_text.return_value = """
    default:
      provider: "openai"
    """
    mock_path.exists.return_value = True
    config = load_llm_config(mock_path)
    registry = LLMRegistry(config)
    
    # Ensure settings has no key
    with patch.object(settings, 'openai_api_key', None):
         with pytest.raises(RuntimeError) as exc:
             registry._base_llm("default")
         assert "OPENAI_API_KEY is not set" in str(exc.value)

def test_llm_registry_unsupported_provider(mock_path):
    """Verifies that LLMRegistry raises ValueError for unsupported providers."""
    mock_path.read_text.return_value = """
    default:
      provider: "anthropic"
      model: "claude-3"
    """
    mock_path.exists.return_value = True
    config = load_llm_config(mock_path)
    registry = LLMRegistry(config)
    
    with pytest.raises(ValueError) as exc:
        registry._base_llm("default")
    assert "Unsupported LLM provider: anthropic" in str(exc.value)
