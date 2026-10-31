"""The Connect routes over a real engine built in a temp folder (docs/architecture/wave_c.md, section 7).

``GET /datasource-types``, ``POST /datasource/test`` and ``POST /datasource`` (with ``save``) are admin-only.
Nothing here reads or writes a real ``.env``: every project is a ``tmp_path`` folder.
"""
import sqlite3

import pytest
import yaml
from dotenv import dotenv_values
from fastapi.testclient import TestClient

from nl2sql import NL2SQL
from nl2sql.config import Api, default_llm, starter_config, write_project
from nl2sql.config.models import ApiProxy
from nl2sql_api.dependencies import get_engine
from nl2sql_api.main import app

PASSWORD = "pw-literal-4242"
ROLE = {"X-Forwarded-User": "ann", "X-NL2SQL-Roles": "admin"}
PROXY = Api(auth="proxy", proxy=ApiProxy(trusted_from=["127.0.0.1/32", "203.0.113.7/32"]))


def _problems(response):
    """The 422's details as {dotted path: message}."""
    return {".".join(problem["loc"]): problem["msg"] for problem in response.json()["error"]["details"]}


@pytest.fixture
def connect(tmp_path):
    made = []

    def _make(host="127.0.0.1", **sections):
        folder = tmp_path / f"project-{len(made)}"
        config = starter_config(llm=default_llm("openai"), path=folder / ".nl2sql").model_copy(update=sections)
        write_project(config, folder)
        engine = NL2SQL.from_config(folder / "nl2sql.yaml")
        app.dependency_overrides[get_engine] = lambda: engine
        client = TestClient(app, base_url="http://localhost", client=(host, 50000))
        made.append((client, engine))
        return client, engine, folder

    yield _make
    app.dependency_overrides.clear()
    for client, engine in made:
        client.close()
        engine.close()


def _database(folder):
    path = folder / "shop.db"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY)")
    connection.commit()
    connection.close()
    return path


def _sqlite(folder, **changes):
    return {"id": "shop", "type": "sqlite", "description": "The shop",
            "connection": {"database": str(_database(folder))}, **changes}


def _postgres(**connection):
    return {"id": "sales", "type": "postgres",
            "connection": {"host": "db.internal", "database": "sales", "user": "app", **connection}}


# ---------------------------------------------------------------- types

def test_types_route(connect):
    client, _, _ = connect()

    response = client.get("/api/v1/datasource-types")

    assert response.status_code == 200, response.text
    types = {t["name"]: t for t in response.json()["types"]}
    assert {"sqlite", "postgres"} <= set(types)
    assert types["sqlite"]["installed"] is True
    assert types["postgres"]["connection_schema"]["properties"]["password"]["format"] == "password"
    assert set(types["sqlite"]) == {"name", "title", "installed", "install_hint", "connection_schema"}


# ---------------------------------------------------------------- test

def test_test_route_registers_nothing(connect):
    client, engine, folder = connect()

    response = client.post("/api/v1/datasource/test", json={"config": _sqlite(folder)})

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ready"
    assert engine.datasources.list() == []
    assert client.get("/api/v1/datasource").json() == {"datasources": []}
    assert not (folder / "datasources" / "shop.yaml").exists()


def test_test_route_reports_the_failed_stage_and_fix(connect):
    client, engine, folder = connect()
    config = _sqlite(folder, connection={"database": str(folder / "missing.db")})

    response = client.post("/api/v1/datasource/test", json={"config": config})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "failed" and body["stage"] and body["reason"] and body["fix"]
    assert engine.datasources.list() == []


def test_test_route_never_echoes_a_secret(connect):
    client, _, _ = connect()

    response = client.post("/api/v1/datasource/test", json={"config": _postgres(password=PASSWORD)})

    assert response.status_code == 200, response.text
    assert PASSWORD not in response.text


def test_test_route_with_a_typo_is_a_422_naming_the_path(connect):
    client, _, folder = connect()

    response = client.post("/api/v1/datasource/test", json={"config": _sqlite(folder, sampel_questions=["x"])})

    assert response.status_code == 422
    assert "config.sampel_questions" in _problems(response)


# ---------------------------------------------------------------- add

def test_add_with_a_typo_is_a_422_naming_the_path(connect):
    client, engine, folder = connect()
    config = _sqlite(folder, sampel_questions=["x"], limits={"row_limit": "SECRET-TYPO-VALUE"})

    response = client.post("/api/v1/datasource", json={"config": config})

    assert response.status_code == 422, response.text
    problems = _problems(response)
    assert set(problems) == {"config.sampel_questions", "config.limits.row_limit"}
    assert all(problems.values()) and "SECRET-TYPO-VALUE" not in response.text
    assert engine.datasources.list() == []


def test_add_without_a_config_is_a_422(connect):
    client, _, _ = connect()

    response = client.post("/api/v1/datasource", json={"save": True})

    assert response.status_code == 422
    assert "config" in _problems(response)


def test_add_with_an_unknown_body_key_is_a_422(connect):
    client, _, folder = connect()

    response = client.post("/api/v1/datasource", json={"config": _sqlite(folder), "sav": True})

    assert response.status_code == 422
    assert "sav" in _problems(response)


def test_add_with_a_taken_id_is_a_422(connect):
    client, _, folder = connect()
    assert client.post("/api/v1/datasource", json={"config": _sqlite(folder)}).status_code == 200

    response = client.post("/api/v1/datasource", json={"config": _sqlite(folder)})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REQUEST"
    assert "shop.db" not in response.text, "the engine's own text is logged, not returned"


def test_add_without_save_writes_no_file(connect):
    client, engine, folder = connect()

    response = client.post("/api/v1/datasource", json={"config": _sqlite(folder)})

    assert response.status_code == 200, response.text
    assert [d.id for d in engine.datasources.list()] == ["shop"]
    assert list((folder / "datasources").iterdir()) == []


# ---------------------------------------------------------------- save

def test_save_on_a_fresh_local_install_writes_env(connect):
    client, engine, folder = connect()

    response = client.post("/api/v1/datasource", json={"config": _postgres(password=PASSWORD), "save": True})

    assert response.status_code == 200, response.text
    assert response.json()["file"] == "datasources/sales.yaml"
    assert PASSWORD not in response.text
    text = (folder / "datasources" / "sales.yaml").read_text(encoding="utf-8")
    assert PASSWORD not in text
    assert yaml.safe_load(text)["connection"]["password"] == "${env:NL2SQL_SALES_PASSWORD}"
    assert dotenv_values(folder / ".env") == {"NL2SQL_SALES_PASSWORD": PASSWORD}
    assert [d.id for d in engine.datasources.list()] == ["sales"]


def test_save_refuses_a_literal_secret_off_loopback(connect):
    # a role source is configured, so this is not the fresh local install: a literal secret never reaches a file
    client, engine, folder = connect(api=PROXY)

    for host in ("127.0.0.1", "203.0.113.7"):
        client = TestClient(app, base_url="http://localhost", client=(host, 50000))
        response = client.post("/api/v1/datasource", json={"config": _postgres(password=PASSWORD), "save": True},
                               headers=ROLE)

        assert response.status_code == 422, response.text
        message = response.json()["error"]["message"] + (response.json()["error"]["fix"] or "")
        assert "password" in message and PASSWORD not in response.text
        assert "${env:" in message
    assert engine.datasources.list() == [], "a datasource that was not saved is not left registered"
    assert list((folder / "datasources").iterdir()) == []
    assert not (folder / ".env").exists()


def test_save_with_a_reference_works_off_loopback(connect, monkeypatch):
    monkeypatch.setenv("SALES_DB_PW", "from-the-environment")
    client, engine, folder = connect(host="203.0.113.7", api=PROXY)

    response = client.post("/api/v1/datasource",
                           json={"config": _postgres(password="${env:SALES_DB_PW}"), "save": True}, headers=ROLE)

    assert response.status_code == 200, response.text
    saved = yaml.safe_load((folder / "datasources" / "sales.yaml").read_text(encoding="utf-8"))
    assert saved["connection"]["password"] == "${env:SALES_DB_PW}"
    assert not (folder / ".env").exists()


def test_save_over_a_file_changed_since_load_is_a_409(connect):
    client, engine, folder = connect()
    (folder / "datasources" / "shop.yaml").write_text("type: sqlite\n", encoding="utf-8")

    response = client.post("/api/v1/datasource", json={"config": _sqlite(folder), "save": True})

    assert response.status_code == 409, response.text
    assert "changed on disk" in response.json()["error"]["message"]
    assert engine.datasources.list() == []
    assert (folder / "datasources" / "shop.yaml").read_text(encoding="utf-8") == "type: sqlite\n"


@pytest.mark.parametrize("path", ["/api/v1/datasource", "/api/v1/datasource/test"])
def test_a_body_that_is_not_an_object_is_our_422_and_echoes_nothing(connect, path):
    client, _, _ = connect()

    for content in (b'["PWLEAK-77"]', b'"PWLEAK-77"', b"42", b"null"):
        response = client.post(path, content=content, headers={"Content-Type": "application/json"})

        assert response.status_code == 422, response.text
        details = response.json()["error"]["details"]
        assert [set(problem) for problem in details] == [{"loc", "msg"}]
        assert details[0]["loc"] == [""] and "object" in details[0]["msg"]
        assert "PWLEAK-77" not in response.text and "input" not in response.text


@pytest.mark.parametrize("path", ["/api/v1/datasource", "/api/v1/datasource/test"])
def test_a_body_that_is_not_json_is_our_422_and_echoes_nothing(connect, path):
    client, _, _ = connect()

    response = client.post(path, content=b'{"config": {"connection": {"password": "PWLEAK-77"',
                           headers={"Content-Type": "application/json"})

    assert response.status_code == 422, response.text
    details = response.json()["error"]["details"]
    assert [set(problem) for problem in details] == [{"loc", "msg"}]
    assert "PWLEAK-77" not in response.text and "input" not in response.text


def test_a_connection_that_does_not_validate_is_the_same_path_message_list(connect):
    client, engine, _ = connect()

    response = client.post("/api/v1/datasource", json={"config": _postgres(passwrd="PWLEAK-77", port="not-a-port")})

    assert response.status_code == 422, response.text
    problems = _problems(response)
    assert {"config.connection.passwrd", "config.connection.port"} <= set(problems)
    assert "passwrd" in problems["config.connection.passwrd"] and "password" in problems["config.connection.passwrd"]
    assert "PWLEAK-77" not in response.text and "not-a-port" not in response.text
    assert engine.datasources.list() == []


def test_a_forwarded_request_is_not_the_local_operator(connect):
    client, engine, folder = connect()

    for header in ({"X-Forwarded-For": "203.0.113.7"}, {"Forwarded": "for=203.0.113.7"}, {"X-Real-IP": "203.0.113.7"}):
        response = client.post("/api/v1/datasource", json={"config": _postgres(password=PASSWORD), "save": True},
                               headers=header)

        assert response.status_code == 403, (header, response.text)
        assert PASSWORD not in response.text
    assert engine.datasources.list() == []
    assert not (folder / ".env").exists()


def test_the_test_route_keeps_a_validation_message_readable(connect):
    client, _, _ = connect()

    response = client.post("/api/v1/datasource/test", json={"config": _postgres(passwrd=PASSWORD)})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "failed" and body["stage"] == "config"
    assert "connection.password: Field required" in body["reason"]
    assert PASSWORD not in response.text


def test_save_of_a_taken_env_name_is_a_422(connect, monkeypatch):
    monkeypatch.setenv("NL2SQL_MY_DB_PASSWORD", "already-set")
    client, engine, folder = connect()
    first = {**_postgres(password="${env:NL2SQL_MY_DB_PASSWORD}"), "id": "my-db"}
    assert client.post("/api/v1/datasource", json={"config": first}).status_code == 200

    second = {**_postgres(password=PASSWORD), "id": "my_db"}
    response = client.post("/api/v1/datasource", json={"config": second, "save": True})

    assert response.status_code == 422, response.text
    assert PASSWORD not in response.text
    assert [d.id for d in engine.datasources.list()] == ["my-db"]
