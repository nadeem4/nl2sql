"""GET /api/v1/index/status reports the engine's real index health, through the facade."""

HEALTH = {
    "status": "stale",
    "total": 87,
    "counts": {"schema.table": 11, "schema.column": 64},
    "built_at": "2026-09-22T12:35:42+00:00",
    "embedding_model": "all-MiniLM-L6-v2",
    "datasources": [{"datasource_id": "chinook", "entries": 87, "index_version": "v1",
                     "snapshot_version": "v2", "built_at": "2026-09-22T12:35:42+00:00"}],
    "problems": ["chinook: the index is behind the latest schema snapshot."],
}


def test_index_status_is_the_engines_index_health(api_client):
    client, engine = api_client()
    engine.index_health = lambda: HEALTH

    response = client.get("/api/v1/index/status")

    assert response.status_code == 200, response.text
    assert response.json() == HEALTH
