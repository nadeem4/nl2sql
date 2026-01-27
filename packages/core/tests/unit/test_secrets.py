
import pytest
from pydantic import SecretStr
from unittest.mock import MagicMock, patch

from nl2sql.datasources.registry import DatasourceRegistry
from nl2sql.datasources.models import ConnectionConfig
from nl2sql.secrets import SecretManager, secret_manager
from nl2sql_postgres.adapter import PostgresAdapter, PostgresConnectionConfig

def test_registry_wraps_secrets():
    """Verify registry wraps resolved secrets in SecretStr."""
    mock_sm = MagicMock(spec=SecretManager)
    mock_sm.resolve.return_value = "super_secret_password"
    registry = DatasourceRegistry(mock_sm)

    unresolved = ConnectionConfig(
        type="postgres",
        host="localhost",
        password="${aws:db_password}",
    )

    resolved = registry.resolved_connection(unresolved)

    assert isinstance(resolved.password, SecretStr)
    assert resolved.password.get_secret_value() == "super_secret_password"
    assert str(resolved.password) == "**********"

def test_postgres_adapter_accepts_secretstr():
    """Verify PostgresAdapter accepts SecretStr and unwraps it for engine creation."""
    connection_args = {
        "host": "localhost",
        "user": "admin",
        "password": SecretStr("my_password"),
        "database": "mydb"
    }
    
    # We mock create_engine to prevent actual connection attempt
    with patch("nl2sql_postgres.adapter.create_engine") as mock_engine:
        adapter = PostgresAdapter(
            datasource_id="test_ds",
            datasource_engine_type="postgres",
            connection_args=connection_args
        )
        
        # Verify uri construction
        expected_uri = "postgresql://admin:my_password@localhost:5432/mydb"
        assert adapter.connection_string == expected_uri

def test_config_hides_secrets():
    """Verify Pydantic model hides secrets in repr."""
    config = PostgresConnectionConfig(
        host="localhost",
        user="admin",
        password=SecretStr("hidden_password"),
        database="db"
    )
    

    assert "hidden_password" not in str(config)
    assert "**********" in str(config)

def test_llm_registry_resolves_secrets():
    """Verify SecretManager resolves SecretStr within Pydantic models."""
    from nl2sql.configs import LLMFileConfig, AgentConfig

    config = LLMFileConfig(
        default=AgentConfig(
            provider="openai",
            model="gpt-4o",
            api_key=SecretStr("${env:TEST_LLM_KEY}")
        )
    )
    
    # Mock resolve
    with patch("nl2sql.secrets.manager.SecretManager.resolve") as mock_resolve:
        mock_resolve.return_value = "sk-resolved-key"
        
        # Test resolve_object directly (ConfigManager uses this)
        resolved_config = secret_manager.resolve_object(config)
        
        # Verify resolution happened inside SecretStr
        assert resolved_config.default.api_key.get_secret_value() == "sk-resolved-key"
        mock_resolve.assert_called_with("${env:TEST_LLM_KEY}")
