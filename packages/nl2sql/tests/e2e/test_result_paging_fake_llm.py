"""Ask a question, then page through its whole result and download it, end to end.

"List every genre" returns Chinook's 25 genres, more than the answer model's
20-row preview. The query response names the saved result in
``result_manifest``; paging through it seven rows at a time, and downloading
it as CSV, each give exactly what running the SQL on the database gives. Both
apps serve it: ``nl2sql-api`` and the playground (which the hosted Space runs).
"""
from __future__ import annotations

import csv
import io
import sqlite3

import pytest

pytest.importorskip("fastapi")

from .test_answer_preview_fake_llm import QUESTION, RULES  # noqa: E402


def _genres(demo_project):
    [db] = list(demo_project.rglob("chinook.sqlite"))
    with sqlite3.connect(db) as conn:
        return [[name] for (name,) in conn.execute("SELECT Name FROM Genre ORDER BY GenreId")]


def _read_back(client, prefix, body):
    """Every page of the run's one result, then its CSV."""
    [entry] = body["result_manifest"]
    assert entry["row_count"] == 25 and entry["columns"] == ["genre_name"] and entry["truncated"] is False
    url = f"{prefix}/query/{body['trace_id']}/results/{entry['node_id']}"
    rows, offset, pages = [], 0, 0
    while offset is not None:
        page = client.get(url, params={"offset": offset, "limit": 7})
        assert page.status_code == 200, page.text
        rows.extend(page.json()["rows"])
        offset, pages = page.json()["next_offset"], pages + 1
    download = client.get(f"{url}.csv")
    assert download.status_code == 200, download.text
    return rows, pages, list(csv.reader(io.StringIO(download.text)))


def _env(monkeypatch, demo_project, env):
    """``fake.yaml`` (from the ``fake_llm`` fixture) with a static ``admin`` role for ``nl2sql-api``, as NL2SQL_CONFIG."""
    import yaml

    path = demo_project / "fake.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["api"] = {"role": "admin"}
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("NL2SQL_CONFIG", str(path))
    return path


@pytest.mark.e2e
def test_the_rest_api_pages_and_exports_the_whole_result(demo_project, fake_llm, monkeypatch):
    from fastapi.testclient import TestClient

    pytest.importorskip("nl2sql_api")
    from nl2sql_api.main import app

    server, env = fake_llm(RULES)
    _env(monkeypatch, demo_project, env)
    with TestClient(app) as client:
        body = client.post("/api/v1/query", json={"natural_language": QUESTION}).json()
        assert body["status"] == "success", body["errors"]
        rows, pages, lines = _read_back(client, "/api/v1", body)

    genres = _genres(demo_project)
    assert len(genres) == 25
    assert rows == genres and pages == 4
    assert lines == [["genre_name"]] + genres


@pytest.mark.e2e
def test_the_playground_pages_and_exports_the_whole_result(demo_project, fake_llm, monkeypatch):
    from fastapi.testclient import TestClient

    from nl2sql import NL2SQL
    from nl2sql.cli.demo.playground.app import build_app

    server, env = fake_llm(RULES)
    _env(monkeypatch, demo_project, env)
    app = build_app(NL2SQL.from_config(), questions=[], roles=["admin"], mode="live", dataset="chinook")
    client = TestClient(app)
    body = client.post("/api/ask", json={"question": QUESTION, "role": "admin"}).json()
    assert body["status"] == "success", body["errors"]
    rows, pages, lines = _read_back(client, "/api", body)
    other = client.get(f"/api/query/{body['trace_id']}/results/nope").status_code

    genres = _genres(demo_project)
    assert rows == genres and pages == 4
    assert lines == [["genre_name"]] + genres
    assert other == 404
