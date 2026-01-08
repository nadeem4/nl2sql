import pytest
from unittest.mock import MagicMock



@pytest.fixture
def mock_profile():
    """Returns a standard SQLite datasource profile for testing."""
    from types import SimpleNamespace
    return SimpleNamespace(
        id="test_sqlite",
        type="sqlite",
        connection={"database": ":memory:"},
        row_limit=100
    )

@pytest.fixture
def mock_engine(mock_profile):
    """Returns an in-memory SQLite engine."""
    from sqlalchemy import create_engine
    return create_engine("sqlite:///:memory:")

@pytest.fixture
def mock_vector_store():
    """Returns a mocked OrchestratorVectorStore."""
    store = MagicMock()
    store.retrieve.return_value = []
    store.is_empty.return_value = False
    return store
