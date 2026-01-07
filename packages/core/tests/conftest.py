import pytest
from unittest.mock import MagicMock
from nl2sql.datasources import DatasourceProfile
# from nl2sql.adapters.sql_generic import SqlGenericAdapter
# TODO: Update tests to usage discovery or mock adapter

@pytest.fixture
def mock_profile():
    """Returns a standard SQLite datasource profile for testing."""
    return DatasourceProfile(
        id="test_sqlite",
        type="sqlite",
        connection={"database": ":memory:"},
        row_limit=100,
        options={}
    )

@pytest.fixture
def mock_engine(mock_profile):
    """Returns an in-memory SQLite engine."""
    from sqlalchemy import create_engine
    from nl2sql.datasources.url_builder import UrlBuilder
    url = UrlBuilder.build(mock_profile.type, mock_profile.connection)
    return create_engine(url)

@pytest.fixture
def mock_vector_store():
    """Returns a mocked OrchestratorVectorStore."""
    store = MagicMock()
    store.retrieve.return_value = []
    store.is_empty.return_value = False
    return store
