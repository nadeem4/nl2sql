
import pytest
from unittest.mock import MagicMock
from nl2sql.datasource_config import DatasourceProfile
from nl2sql.engine_factory import make_engine

@pytest.fixture
def mock_profile():
    """Returns a standard SQLite datasource profile for testing."""
    return DatasourceProfile(
        id="test_sqlite",
        engine="sqlite",
        sqlalchemy_url="sqlite:///:memory:",
        row_limit=100,
        auth=None,
        read_only_role=None
    )

@pytest.fixture
def mock_engine(mock_profile):
    """Returns an in-memory SQLite engine."""
    return make_engine(mock_profile)

@pytest.fixture
def mock_vector_store():
    """Returns a mocked SchemaVectorStore."""
    store = MagicMock()
    store.retrieve.return_value = []
    store.is_empty.return_value = False
    return store
