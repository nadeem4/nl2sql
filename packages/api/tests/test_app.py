"""App wiring tests (ported from packages/api/test_api.py)."""

import inspect
from importlib.metadata import PackageNotFoundError, version

from nl2sql_api import dependencies, main
from nl2sql_api.main import app
from nl2sql_api.routes import datasource, indexing, llm, query


def test_app_is_constructed():
    assert app is not None
    assert app.title == "NL2SQL API"


def test_dependency_providers_are_callable():
    assert callable(dependencies.get_engine)
    assert callable(dependencies.get_query_service)


def test_health_endpoint_is_registered():
    paths = app.openapi()["paths"]
    assert "/api/v1/health" in paths
    assert "/api/v1/query" in paths


def test_routes_doing_blocking_work_are_synchronous():
    """`async def` handlers around blocking calls would serialise the whole API."""
    for module in (query, datasource, llm, indexing):
        for name, func in vars(module).items():
            if name.startswith("_") or not inspect.isfunction(func):
                continue
            assert not inspect.iscoroutinefunction(func), f"{module.__name__}.{name} is async"


def test_app_version_matches_installed_distribution():
    """The OpenAPI version must track the distribution, not a hardcoded literal."""
    assert app.version == version("nl2sql-api")


def test_app_version_falls_back_when_distribution_is_missing(monkeypatch):
    """Importing from a source tree with nl2sql-api uninstalled must not blow up."""

    def _raise(name):
        raise PackageNotFoundError(name)

    monkeypatch.setattr(main, "version", _raise)
    assert main._app_version()
