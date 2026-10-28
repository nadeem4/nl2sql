"""``engine.datasources.save`` (docs/architecture/wave_c.md, row L2): one datasource as ``datasources/<id>.yaml``.

Temp folders only: nothing here reads or writes a real ``.env``.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml
from dotenv import dotenv_values

from nl2sql import NL2SQL, SecretValueInConfig, UserContext
from nl2sql.config import (Datasource, Policies, Role, default_llm, load_config, starter_config, write_project)
from nl2sql.review.models import ReviewError, ReviewErrorCode
from nl2sql.testing.config import make_config

ADMIN = UserContext(user_id="root", roles=["admin"])
ANALYST = UserContext(user_id="ann", roles=["analyst"])
PASSWORD = "pw-literal-9999"


def _roles() -> Policies:
    return Policies(roles={"admin": Role(description="Everything", allow=["*"]),
                           "analyst": Role(description="Reads", allow=["*"])})


def _shop(**changes) -> Datasource:
    fields = {"id": "shop", "type": "sqlite", "description": "The shop", "connection": {"database": "shop.db"}}
    return Datasource(**{**fields, **changes})


def _postgres(datasource_id: str, **connection) -> Datasource:
    return Datasource(id=datasource_id, type="postgres", connection={
        "host": "db.internal", "database": "sales", "user": "app", **connection})


@pytest.fixture
def project(tmp_path):
    """A project folder with no datasources and admin/analyst roles, and the engine loaded from it."""
    config = starter_config(llm=default_llm("openai"), path=tmp_path / ".nl2sql").model_copy(
        update={"policies": _roles()})
    write_project(config, tmp_path)
    engine = NL2SQL.from_config(tmp_path / "nl2sql.yaml")
    yield tmp_path, engine
    engine.close()


def test_save_writes_one_datasource_file(project):
    folder, engine = project
    engine.datasources.add(_shop(sample_questions=["How many orders?"]))
    before = (folder / "nl2sql.yaml").read_bytes()

    path = engine.datasources.save("shop", by=ADMIN)

    assert path == folder / "datasources" / "shop.yaml"
    assert yaml.safe_load(path.read_text(encoding="utf-8")) == {
        "type": "sqlite", "description": "The shop", "connection": {"database": "shop.db"},
        "sample_questions": ["How many orders?"]}
    assert (folder / "nl2sql.yaml").read_bytes() == before
    assert [d.id for d in load_config(folder / "nl2sql.yaml").datasources] == ["shop"]
    assert sorted(p.name for p in (folder / "datasources").iterdir()) == ["shop.yaml"], "no temporary file is left"


def test_save_publishes_datasource_saved(project):
    folder, engine = project
    seen = []
    engine.events.subscribe(seen.append, names=["datasource.saved"])
    engine.datasources.add(_shop())

    engine.datasources.save("shop", by=ADMIN)

    assert [(e.name, e.data["datasource_id"], e.data["file"], e.data["by"]) for e in seen] == [
        ("datasource.saved", "shop", "datasources/shop.yaml", "root")]


def test_save_needs_an_admin(project):
    folder, engine = project
    engine.datasources.add(_shop())

    with pytest.raises(PermissionError, match="admin"):
        engine.datasources.save("shop", by=ANALYST)
    with pytest.raises(TypeError):
        engine.datasources.save("shop", by="root")  # type: ignore[arg-type]

    assert list((folder / "datasources").iterdir()) == []


def test_save_refuses_an_unknown_datasource(project):
    _, engine = project

    with pytest.raises(ValueError, match="Unknown datasource ID: nope"):
        engine.datasources.save("nope", by=ADMIN)


def test_save_refuses_a_file_changed_since_load(tmp_path):
    config = starter_config(llm=default_llm("openai"), path=tmp_path / ".nl2sql").with_datasource(_shop())
    write_project(config, tmp_path)
    engine = NL2SQL.from_config(tmp_path / "nl2sql.yaml")
    try:
        target = tmp_path / "datasources" / "shop.yaml"
        target.write_text("type: sqlite\ndescription: edited by a person\nconnection: {database: shop.db}\n",
                          encoding="utf-8")

        with pytest.raises(ReviewError) as raised:
            engine.datasources.save("shop", by=ADMIN)

        assert raised.value.code is ReviewErrorCode.FILE_CHANGED
        assert "reload" in raised.value.fix.lower()
        assert "edited by a person" in target.read_text(encoding="utf-8")
    finally:
        engine.close()


def test_save_over_the_file_it_loaded_works_again_and_again(tmp_path):
    config = starter_config(llm=default_llm("openai"), path=tmp_path / ".nl2sql").with_datasource(_shop())
    write_project(config, tmp_path)
    engine = NL2SQL.from_config(tmp_path / "nl2sql.yaml")
    try:
        engine.datasources.save("shop", by=ADMIN)
        engine.datasources.save("shop", by=ADMIN)  # the second save sees the hash of the first
    finally:
        engine.close()


def test_a_new_datasource_file_must_not_exist_yet(project):
    folder, engine = project
    engine.datasources.add(_shop())
    (folder / "datasources" / "shop.yaml").write_text("type: sqlite\n", encoding="utf-8")

    with pytest.raises(ReviewError) as raised:
        engine.datasources.save("shop", by=ADMIN)

    assert raised.value.code is ReviewErrorCode.FILE_CHANGED


def test_save_needs_a_folder_include(tmp_path):
    inline = make_config(tmp_path / "inline", datasources=[
        {"id": "shop", "type": "sqlite", "connection": {"database": "shop.db"}}])
    for name, config in {"inline": inline, "code": inline.model_copy()}.items():
        engine = NL2SQL(config) if name == "code" else NL2SQL.from_config(tmp_path / "inline" / "nl2sql.yaml")
        try:
            with pytest.raises(ValueError, match="datasources/"):
                engine.datasources.save("shop", by=ADMIN)
        finally:
            engine.close()


def test_a_secret_literal_is_refused_without_env_file(project):
    folder, engine = project
    engine.datasources.add(_postgres("sales", password=PASSWORD))

    with pytest.raises(SecretValueInConfig) as raised:
        engine.datasources.save("sales", by=ADMIN)

    assert "password" in str(raised.value) and PASSWORD not in str(raised.value)
    assert list((folder / "datasources").iterdir()) == []
    assert not (folder / ".env").exists()


def test_a_reference_is_saved_as_it_is_without_env_file(project, monkeypatch):
    folder, engine = project
    monkeypatch.setenv("SALES_DB_PW", "from-the-environment")
    engine.datasources.add(_postgres("sales", password="${env:SALES_DB_PW}"))

    path = engine.datasources.save("sales", by=ADMIN)

    assert yaml.safe_load(path.read_text(encoding="utf-8"))["connection"]["password"] == "${env:SALES_DB_PW}"
    assert not (folder / ".env").exists()


def test_env_file_writes_the_secret_to_env_and_a_reference_to_yaml(project):
    folder, engine = project
    (folder / ".env").write_text("# mine\nOPENAI_API_KEY=keep-me\n", encoding="utf-8")
    engine.datasources.add(_postgres("sales", password=PASSWORD))
    before = dict(os.environ)

    path = engine.datasources.save("sales", by=ADMIN, env_file=True)

    assert PASSWORD not in path.read_text(encoding="utf-8")
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["connection"]["password"] == "${env:NL2SQL_SALES_PASSWORD}"
    assert dotenv_values(folder / ".env") == {"OPENAI_API_KEY": "keep-me", "NL2SQL_SALES_PASSWORD": PASSWORD}
    assert (folder / ".env").read_text(encoding="utf-8").startswith("# mine\n")
    assert dict(os.environ) == before, "the process environment is never written"
    assert ".env" in (folder / ".gitignore").read_text(encoding="utf-8").splitlines()


def test_a_name_another_datasource_uses_is_refused_not_overwritten(project, monkeypatch):
    folder, engine = project
    monkeypatch.setenv("NL2SQL_MY_DB_PASSWORD", "already-set")
    engine.datasources.add(_postgres("my-db", password="${env:NL2SQL_MY_DB_PASSWORD}"))
    engine.datasources.add(_postgres("my_db", password=PASSWORD))

    with pytest.raises(ValueError, match="my-db.*my_db|my_db.*my-db"):
        engine.datasources.save("my_db", by=ADMIN, env_file=True)

    assert not (folder / ".env").exists()
    assert list((folder / "datasources").iterdir()) == []


def test_a_datasource_id_that_is_not_a_file_name_is_refused(project):
    folder, engine = project
    engine.datasources.add(_shop(id="../escape"))

    with pytest.raises(ValueError, match="file name"):
        engine.datasources.save("../escape", by=ADMIN)

    assert not (folder.parent / "escape.yaml").exists()


def test_save_never_calls_a_model_or_a_database(project):
    folder, engine = project
    seen = []
    engine.events.subscribe(seen.append)
    engine.datasources.add(_shop())

    engine.datasources.save("shop", by=ADMIN)

    assert not [e.name for e in seen if e.name.startswith(("llm.", "datasource.connect", "run."))]


def test_testing_a_datasource_never_echoes_its_secret(project):
    from nl2sql_sdk import ConnectionCheck

    class EchoingAdapter:
        def test_connection(self):
            return ConnectionCheck(ok=False, stage="auth", message=f"login failed for the password {PASSWORD}",
                                   fix=f"Try {PASSWORD} again")

    _, engine = project
    datasource = Datasource(id="sales", adapter=EchoingAdapter(), connection={"password": PASSWORD})

    status = engine.datasources.test(datasource)

    assert status.status == "failed" and status.stage == "auth"
    assert PASSWORD not in f"{status.reason} {status.fix}"
    assert engine.datasources.list() == [], "testing registers nothing"


def test_the_saved_datasource_wins_after_a_reload(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-not-a-real-key")
    write_project(starter_config(llm=default_llm("openai"), path=tmp_path / ".nl2sql"), tmp_path)
    engine = NL2SQL.from_config(tmp_path / "nl2sql.yaml")
    try:
        engine.datasources.add(_postgres("sales", password=PASSWORD))
        engine.datasources.save("sales", by=ADMIN, env_file=True)
    finally:
        engine.close()
    monkeypatch.setenv("NL2SQL_SALES_PASSWORD", dotenv_values(tmp_path / ".env")["NL2SQL_SALES_PASSWORD"])

    again = NL2SQL.from_config(tmp_path / "nl2sql.yaml")
    try:
        assert [d.id for d in again.datasources.list()] == ["sales"]
        assert again.config.datasources[0].connection["password"] == "${env:NL2SQL_SALES_PASSWORD}"
    finally:
        again.close()
