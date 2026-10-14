import pytest
from fastapi.testclient import TestClient

from nl2sql import UserContext
from nl2sql_api.auth import get_user_context, require_admin
from nl2sql_api.dependencies import get_engine
from nl2sql_api.main import app
from nl2sql.testing.config import make_config


class StubEngine:
    """Stands in for NL2SQL so no datasource, LLM or network is required.

    ``config`` is a real ``Config``, loaded from a ``make_config`` folder: the
    API reads its ``api`` section and ``policies.admin_roles``.
    """

    def __init__(self, run_query_result=None, config=None):
        self.run_query_result = run_query_result
        self.config = config
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

    def stream_query(self, natural_language, datasource_id=None, execute=True, user_context=None,
                     cancellation_token=None):
        """The run as events: one per node, then ``final`` with ``run_query``'s result."""
        from nl2sql import QueryEvent

        self.calls.append({"natural_language": natural_language, "datasource_id": datasource_id,
                           "execute": execute, "user_context": user_context, "stream": True,
                           "cancellation_token": cancellation_token})
        if isinstance(self.run_query_result, Exception):
            raise self.run_query_result
        yield QueryEvent(type="node_started", data={"node": "decomposer"})
        yield QueryEvent(type="node_finished", data={"node": "decomposer", "seconds": 0.1, "ok": True})
        yield QueryEvent(type="answer_delta", data={"field": "summary", "text": "Revenue "})
        yield QueryEvent(type="final", data=(self.run_query_result.model_dump(mode="json")
                                             if self.run_query_result is not None else {}))


@pytest.fixture
def api_client(tmp_path):
    """Build a TestClient plus the StubEngine backing it.

    ``TestClient`` is used without its context manager so the app lifespan (which
    would construct a real NL2SQL engine) never runs. With ``authenticated``
    (the default) the caller holds the ``admin`` role; pass False to exercise
    the real ``get_user_context`` dependency. ``sections`` are written into the
    engine's ``nl2sql.yaml`` (``api=...``, ``policies=...``), as ``make_config`` takes them.
    """
    clients = []

    def _make(run_query_result=None, authenticated=True, **sections):
        engine = StubEngine(run_query_result, config=make_config(tmp_path / f"project-{len(clients)}", **sections))
        app.dependency_overrides[get_engine] = lambda: engine
        app.state.engine = engine
        if authenticated:
            app.dependency_overrides[get_user_context] = lambda: UserContext(roles=["admin"])
            app.dependency_overrides[require_admin] = lambda: UserContext(roles=["admin"])
        client = TestClient(app)
        clients.append(client)
        return client, engine

    yield _make

    app.dependency_overrides.clear()
    if hasattr(app.state, "engine"):
        del app.state.engine
    for client in clients:
        client.close()
