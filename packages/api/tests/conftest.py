import pytest
from fastapi.testclient import TestClient

from nl2sql import UserContext
from nl2sql_api.auth import get_user_context
from nl2sql_api.dependencies import get_engine
from nl2sql_api.main import app


class StubEngine:
    """Stands in for NL2SQL so no datasource, LLM or network is required."""

    def __init__(self, run_query_result=None):
        self.run_query_result = run_query_result
        self.calls = []

    def run_query(self, natural_language, datasource_id=None, execute=True, user_context=None):
        self.calls.append(
            {
                "natural_language": natural_language,
                "datasource_id": datasource_id,
                "execute": execute,
                "user_context": user_context,
            }
        )
        if isinstance(self.run_query_result, Exception):
            raise self.run_query_result
        return self.run_query_result


@pytest.fixture
def api_client():
    """Build a TestClient plus the StubEngine backing it.

    ``TestClient`` is used without its context manager so the app lifespan (which
    would construct a real NL2SQL engine) never runs. With ``authenticated``
    (the default) the caller holds the ``admin`` role; pass False to exercise
    the real ``get_user_context`` dependency.
    """
    clients = []

    def _make(run_query_result=None, authenticated=True):
        engine = StubEngine(run_query_result)
        app.dependency_overrides[get_engine] = lambda: engine
        if authenticated:
            app.dependency_overrides[get_user_context] = lambda: UserContext(roles=["admin"])
        client = TestClient(app)
        clients.append(client)
        return client, engine

    yield _make

    app.dependency_overrides.clear()
    for client in clients:
        client.close()
