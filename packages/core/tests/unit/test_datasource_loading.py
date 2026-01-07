import pathlib
import os
import pytest
from unittest.mock import MagicMock, patch
from nl2sql.datasources.config import load_configs
from nl2sql.datasources.registry import DatasourceRegistry
from nl2sql_adapter_sdk import DatasourceAdapter

class MockAdapter(DatasourceAdapter):
    """Mock adapter for testing registry initialization."""
    @property
    def datasource_id(self) -> str:
        return self._id

    @property
    def row_limit(self) -> int:
        return self.kwargs.get("row_limit", 100)

    @property
    def max_bytes(self) -> int:
        return self.kwargs.get("max_bytes", 1000)

    def __init__(self, datasource_id: str, datasource_engine_type: str, connection_args: dict, **kwargs):
        self._id = datasource_id
        self.datasource_engine_type = datasource_engine_type
        self.connection_args = connection_args
        self.kwargs = kwargs
    
    def fetch_schema(self):
        return None
    
    def execute(self, query):
        return []
        
    def get_dialect(self):
        return "mock"
        
    def connect(self):
        pass
        
    def dry_run(self, sql):
        return True
        
    def cost_estimate(self, sql):
        return 0.0
        
    def explain(self, sql):
        return "mock plan"

@pytest.fixture
def mock_path():
    """Returns a MagicMock simulating a pathlib.Path object."""
    return MagicMock(spec=pathlib.Path)

def test_load_configs_success(mock_path):
    """Verifies that load_configs correctly parses a valid YAML file."""
    mock_path.exists.return_value = True
    mock_path.read_text.return_value = """
    datasources:
      - id: "test_ds"
        connection: 
          type: "sqlite"
    """
    
    configs = load_configs(mock_path)
    
    assert isinstance(configs, list)
    assert len(configs) == 1
    assert configs[0]["id"] == "test_ds"

def test_load_configs_file_not_found(mock_path):
    """Verifies that load_configs raises FileNotFoundError when path is invalid."""
    mock_path.exists.return_value = False
    
    with pytest.raises(FileNotFoundError):
        load_configs(mock_path)

def test_load_configs_invalid_structure(mock_path):
    """Verifies that load_configs raises ValueError if YAML structure is incorrect."""
    mock_path.exists.return_value = True
    mock_path.read_text.return_value = "invalid_key: value"
    
    with pytest.raises(ValueError):
        load_configs(mock_path)

def test_registry_resolves_env_secrets():
    """Verifies that DatasourceRegistry resolves ${env:VAR} secrets during initialization."""
    config = {
        "id": "secure_ds",
        "connection": {
            "type": "mock_db",
            "password": "${env:TEST_SECRET}"
        }
    }
    
    with patch.dict(os.environ, {"TEST_SECRET": "resolved_value"}):
        with patch("nl2sql.datasources.registry.discover_adapters", return_value={"mock_db": MockAdapter}):
            registry = DatasourceRegistry([config])
            adapter = registry.get_adapter("secure_ds")
            
            assert adapter.connection_args["password"] == "resolved_value"

def test_registry_ignores_partial_secrets():
    """Verifies that DatasourceRegistry does not resolve partial secret strings."""
    config = {
        "id": "partial_ds",
        "connection": {
            "type": "mock_db",
            "host": "prefix_${env:VAR}"
        }
    }
    
    with patch.dict(os.environ, {"VAR": "value"}):
        with patch("nl2sql.datasources.registry.discover_adapters", return_value={"mock_db": MockAdapter}):
            registry = DatasourceRegistry([config])
            adapter = registry.get_adapter("partial_ds")
            
            assert adapter.connection_args["host"] == "prefix_${env:VAR}"

def test_registry_missing_secret_error():
    """Verifies that DatasourceRegistry raises ValueError when a secret is missing."""
    config = {
        "id": "fail_ds",
        "connection": {
            "type": "mock_db",
            "token": "${env:MISSING_VAR}"
        }
    }
    
    with patch("nl2sql.datasources.registry.discover_adapters", return_value={"mock_db": MockAdapter}):
        with pytest.raises(ValueError) as exc:
            DatasourceRegistry([config])
        assert "Secret not found: env:MISSING_VAR" in str(exc.value)

def test_registry_init_success():
    """Verifies successful adapter initialization with all configuration parameters."""
    config = {
        "id": "full_ds",
        "row_limit": 500,
        "max_bytes": 1024,
        "statement_timeout_ms": 2000,
        "connection": {"type": "mock_db"}
    }
    
    with patch("nl2sql.datasources.registry.discover_adapters", return_value={"mock_db": MockAdapter}):
        registry = DatasourceRegistry([config])
        adapter = registry.get_adapter("full_ds")
        
        assert isinstance(adapter, MockAdapter)
        assert adapter.kwargs["row_limit"] == 500
        assert adapter.kwargs["max_bytes"] == 1024
        assert adapter.kwargs["statement_timeout_ms"] == 2000

def test_registry_unknown_adapter_type():
    """Verifies that DatasourceRegistry raises ValueError for unknown adapter types."""
    config = {
        "id": "bad_type_ds",
        "connection": {"type": "unknown_db"}
    }
    
    with patch("nl2sql.datasources.registry.discover_adapters", return_value={"mock_db": MockAdapter}):
        with pytest.raises(ValueError) as exc:
            DatasourceRegistry([config])
        assert "No adapter found for engine type: 'unknown_db'" in str(exc.value)

def test_registry_missing_id():
    """Verifies that DatasourceRegistry raises ValueError if datasource ID is missing."""
    config = {
        "connection": {"type": "mock_db"}
    }
    
    with pytest.raises(ValueError) as exc:
        DatasourceRegistry([config])
    assert "Datasource ID is required" in str(exc.value)
