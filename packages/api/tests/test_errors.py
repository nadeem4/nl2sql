"""One error envelope for every non-2xx response (docs/architecture/wave_d.md, D202, B200).

Only the facade's own ``NL2SQLError`` is mapped to its status; anything else is a 500 that names a request id and
never its text.
"""
import logging

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient
from pydantic import BaseModel, ValidationError

from nl2sql import (ApprovalNotFound, DashboardBusy, InvalidArgument, NL2SQLError, NotAuthorized, UserContext)
from nl2sql.testing.config import make_config
from nl2sql_api import identity
from nl2sql_api.dependencies import get_engine
from nl2sql_api.main import create_app

SECRET = "postgresql://admin:hunter2@db.internal/sales"
ENVELOPE_KEYS = {"code", "message", "fix", "details", "request_id"}


class _Body(BaseModel):
    count: int


def _validation_error() -> ValidationError:
    try:
        _Body(count=SECRET)
    except ValidationError as error:
        return error
    raise AssertionError("not reached")


@pytest.fixture
def make_app(tmp_path):
    """An app with one route, ``/boom``, that raises what the test says; no engine is built."""
    def _make(raises=None):
        app = create_app()
        router = APIRouter()

        @router.get("/boom")
        def boom():
            raise raises

        @router.post("/typed")
        def typed(body: _Body):
            return body

        app.include_router(router, prefix="/test")
        app.dependency_overrides[get_engine] = lambda: None
        return app, TestClient(app, raise_server_exceptions=False)
    return _make


def test_error_envelope_shape(make_app):
    _, client = make_app(ApprovalNotFound("No approval 'a1'.", fix="List the approvals first."))
    response = client.get("/test/boom")
    assert response.status_code == 404
    assert response.json() == {"error": {"code": "NOT_FOUND", "message": "No approval 'a1'.",
                                         "fix": "List the approvals first.", "details": [], "request_id": None}}
    assert set(response.json()["error"]) == ENVELOPE_KEYS


@pytest.mark.parametrize("error,status,code", [
    (NotAuthorized("Admins only."), 403, "NOT_AUTHORIZED"),
    (ApprovalNotFound("Gone."), 404, "NOT_FOUND"),
    (DashboardBusy("Refreshing."), 409, "CONFLICT"),
    (InvalidArgument("Bad name."), 422, "INVALID_ARGUMENT"),
])
def test_nl2sql_error_maps_to_its_status(make_app, error, status, code):
    _, client = make_app(error)
    response = client.get("/test/boom")
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert response.json()["error"]["message"] == str(error)


def test_validation_error_never_echoes_input(make_app):
    _, client = make_app()
    response = client.post("/test/typed", json={"count": SECRET})
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "INVALID_REQUEST"
    assert error["details"] == [{"loc": ["body", "count"], "msg": "Input should be a valid integer, "
                                                                   "unable to parse string as an integer"}]
    assert SECRET not in response.text and "hunter2" not in response.text


def test_a_body_that_is_not_json_is_a_422_without_the_input(make_app):
    _, client = make_app()
    response = client.post("/test/typed", content=f"{{{SECRET}", headers={"content-type": "application/json"})
    assert response.status_code == 422
    assert "hunter2" not in response.text


def test_builtin_lookup_error_is_500(make_app, caplog):
    _, client = make_app(LookupError(SECRET))
    with caplog.at_level(logging.ERROR):
        response = client.get("/test/boom")
    assert response.status_code == 500
    error = response.json()["error"]
    assert error["code"] == "INTERNAL"
    assert error["request_id"] and error["request_id"] in error["message"]
    assert response.headers["X-Request-ID"] == error["request_id"]
    assert "hunter2" not in response.text
    assert error["request_id"] in caplog.text and "hunter2" in caplog.text  # the cause is in the log, not the body


def test_the_base_class_alone_is_an_internal_error(make_app):
    _, client = make_app(NL2SQLError("an internal detail"))
    response = client.get("/test/boom")
    assert response.status_code == 500
    assert "an internal detail" not in response.text


def test_an_unknown_route_uses_the_envelope(make_app):
    _, client = make_app()
    response = client.get("/api/v1/nothing-here")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_every_response_carries_a_request_id(make_app):
    _, client = make_app()
    assert client.get("/api/v1/health").headers["X-Request-ID"]


class _Exploding:
    """An engine whose every call raises ``error``; ``config`` is real so the identity checks still work."""

    def __init__(self, error, config):
        self._error = error
        self.config = config

    def __getattr__(self, name):
        error = self._error

        class Anything:
            def __getattr__(self, name):
                return self

            def __call__(self, *args, **kwargs):
                raise error

            def __iter__(self):
                raise error

        return Anything()


EXCEPTIONS = {
    "ValueError": lambda: ValueError(SECRET),
    "ValidationError": _validation_error,
    "KeyError": lambda: KeyError(SECRET),
    "PermissionError": lambda: PermissionError(SECRET),
}


# Routes that do not call the engine, and the stream, which reports a failure as its last event.
ANSWERS_200 = {"/api/v1/health", "/api/v1/ready", "/api/v1/meta", "/api/v1/query/stream"}


@pytest.mark.parametrize("kind", EXCEPTIONS)
def test_no_route_returns_exception_text(tmp_path, kind):
    """B200: through every registered route, a built-in exception carrying a connection string is a 500 (or a
    refusal that says nothing of it), and no part of the string is in the body."""
    app = create_app()
    config = make_config(tmp_path / "project")
    app.dependency_overrides[get_engine] = lambda: _Exploding(EXCEPTIONS[kind](), config)
    app.dependency_overrides[identity.caller] = lambda: UserContext(user_id="u", roles=["admin"])
    app.dependency_overrides[identity.admin] = lambda: UserContext(user_id="u", roles=["admin"])
    client = TestClient(app, raise_server_exceptions=False)
    body = {"natural_language": "q", "config": {"id": "ds", "type": "sqlite", "connection": {"database": "x.db"}}}
    routes = [(method.upper(), path.replace("{", "").replace("}", "")) for path, operations in app.openapi()["paths"].items()
              if path.startswith("/api/v1") for method in operations if method in {"get", "post", "put", "delete"}]
    assert routes
    for method, path in routes:
        response = client.request(method, path, json=body if method in {"POST", "PUT"} else None)
        assert "hunter2" not in response.text, f"{method} {path} answered with the exception text"
        assert response.status_code != 200 or path in ANSWERS_200, f"{method} {path}: {response.status_code}"
