"""The interactive docs: Swagger UI at /docs, ReDoc at /redoc, and a schema that documents every route."""

from fastapi.routing import APIRoute

from nl2sql_api.routes import datasource, health, indexing, llm, query

PREFIX = "/api/v1"


def _routes():
    """Every (method, path) the routers declare, as main.py mounts them under /api/v1."""
    return [(method, PREFIX + route.path) for module in (query, health, datasource, llm, indexing)
            for route in module.router.routes if isinstance(route, APIRoute) for method in sorted(route.methods)]


def test_swagger_ui_redoc_and_the_schema_are_served(api_client):
    client, _ = api_client()
    assert "swagger-ui" in client.get("/docs").text
    assert "redoc" in client.get("/redoc").text.lower()
    assert client.get("/openapi.json").status_code == 200


def test_the_schema_lists_every_route_with_a_summary_and_a_description(api_client):
    client, _ = api_client()
    paths = client.get("/openapi.json").json()["paths"]
    routes = _routes()
    assert len(routes) == 14
    assert sum(len(ops) for ops in paths.values()) == len(routes)
    for method, path in routes:
        operation = paths[path][method.lower()]
        assert operation.get("summary"), f"{method} {path} has no summary"
        assert operation.get("description"), f"{method} {path} has no description"
        assert operation.get("tags"), f"{method} {path} has no tag"


def test_the_query_request_and_response_models_are_in_the_schema(api_client):
    client, _ = api_client()
    schema = client.get("/openapi.json").json()
    query = schema["paths"]["/api/v1/query"]["post"]
    assert query["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith("/QueryRequest")
    assert query["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/QueryResponse")
    assert {"QueryRequest", "QueryResponse", "SubQueryResponse"} <= set(schema["components"]["schemas"])
