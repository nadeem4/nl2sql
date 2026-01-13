
import pytest
from pydantic import SecretStr
from unittest.mock import MagicMock, patch
from nl2sql.datasources.registry import DatasourceRegistry
from nl2sql_postgres.adapter import PostgresAdapter, PostgresConnectionConfig

def test_registry_wraps_secrets():
    """Verify registry wraps resolved secrets in SecretStr."""
    configs = [] 
    registry = DatasourceRegistry(configs)
    
    # Mock secret manager
    with patch("nl2sql.datasources.registry.secret_manager") as mock_sm:
        mock_sm.resolve.return_value = "super_secret_password"
        
        unresolved = {
            "host": "localhost",
            "password": "${aws:db_password}"
        }
        
        resolved = registry.resolved_connection(unresolved)
        
        assert isinstance(resolved["password"], SecretStr)
        assert resolved["password"].get_secret_value() == "super_secret_password"
        assert str(resolved["password"]) == "**********"

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
