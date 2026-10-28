"""``engine.datasources.save`` (docs/architecture/wave_c.md, row L2): one datasource as ``datasources/<id>.yaml``.

Temp folders only: nothing here reads or writes a real ``.env``.
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Optional

import pytest
import yaml
from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, SecretStr

from nl2sql import NL2SQL, Registry, SecretValueInConfig, UserContext
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


# ---------------------------------------------------------------- review fixes

class VaultConnection(BaseModel):
    """A plugin's connection: its secret fields are not named like secrets."""

    model_config = ConfigDict(extra="forbid")
    host: str
    private_key: Optional[SecretStr] = None
    api_key: Optional[SecretStr] = None


class VaultAdapter:
    interface_version = 2
    title = "Vault DB"
    connection_model = VaultConnection
    driver_modules = ()
    install_hint = ""

    def __init__(self, datasource_id=None, connection=None, limits=None, *, folder=None):
        self.datasource_id = datasource_id

    def capabilities(self):
        return set()

    def close(self):
        pass


class EchoingVaultAdapter(VaultAdapter):
    def test_connection(self):
        from nl2sql_sdk import ConnectionCheck

        return ConnectionCheck(ok=False, stage="auth", message="bad key PK-LITERAL-0001", fix=None)


@pytest.fixture
def plugin_project(tmp_path):
    registry = Registry.default()
    registry.add_adapter("vaultdb", VaultAdapter)
    registry.add_adapter("echo", EchoingVaultAdapter)
    config = starter_config(llm=default_llm("openai"), path=tmp_path / ".nl2sql").model_copy(
        update={"policies": _roles()})
    write_project(config, tmp_path)
    engine = NL2SQL.from_config(tmp_path / "nl2sql.yaml", registry=registry)
    yield tmp_path, engine
    engine.close()


def test_a_plugin_secret_field_not_named_like_a_secret_goes_to_env(plugin_project):
    folder, engine = plugin_project
    engine.datasources.add(Datasource(id="vault", type="vaultdb", connection={
        "host": "h", "private_key": "PK-LITERAL-0001"}))

    path = engine.datasources.save("vault", by=ADMIN, env_file=True)

    text = path.read_text(encoding="utf-8")
    assert "PK-LITERAL-0001" not in text
    assert yaml.safe_load(text)["connection"]["private_key"] == "${env:NL2SQL_VAULT_PRIVATE_KEY}"
    assert dotenv_values(folder / ".env") == {"NL2SQL_VAULT_PRIVATE_KEY": "PK-LITERAL-0001"}


def test_a_plugin_secret_field_literal_is_refused_without_env_file(plugin_project):
    folder, engine = plugin_project
    engine.datasources.add(Datasource(id="vault", type="vaultdb", connection={
        "host": "h", "private_key": "PK-LITERAL-0001"}))

    with pytest.raises(SecretValueInConfig) as raised:
        engine.datasources.save("vault", by=ADMIN)

    assert "private_key" in str(raised.value) and "PK-LITERAL-0001" not in str(raised.value)
    assert list((folder / "datasources").iterdir()) == []


def test_testing_a_plugin_datasource_never_echoes_its_secret_field(plugin_project):
    folder, engine = plugin_project

    status = engine.datasources.test(Datasource(id="e", type="echo", connection={
        "host": "h", "private_key": "PK-LITERAL-0001"}))

    assert "PK-LITERAL-0001" not in f"{status.reason} {status.fix}"


def test_two_concurrent_saves_both_keep_their_env_line(project, monkeypatch):
    import nl2sql.config.writer as writer

    folder, engine = project
    engine.datasources.add(_postgres("sales", password="pw-sales-0001"))
    engine.datasources.add(_postgres("stock", password="pw-stock-0002"))
    real = writer.write_atomic

    def slow(path, data):
        time.sleep(0.3)  # both saves are inside the read-modify-write at once, unless one waits for the other
        real(path, data)

    monkeypatch.setattr(writer, "write_atomic", slow)
    errors = []

    def save(name):
        try:
            engine.datasources.save(name, by=ADMIN, env_file=True)
        except BaseException as error:  # noqa: BLE001 - reported below
            errors.append(error)

    threads = [threading.Thread(target=save, args=(name,)) for name in ("sales", "stock")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert dotenv_values(folder / ".env") == {"NL2SQL_SALES_PASSWORD": "pw-sales-0001",
                                              "NL2SQL_STOCK_PASSWORD": "pw-stock-0002"}


def test_a_datasource_named_like_the_llm_cannot_replace_its_key(plugin_project):
    folder, engine = plugin_project
    engine.datasources.add(Datasource(id="llm", type="vaultdb", connection={"host": "h", "api_key": "K-LITERAL-0009"}))

    with pytest.raises(ValueError, match="NL2SQL_LLM_API_KEY"):
        engine.datasources.save("llm", by=ADMIN, env_file=True)

    assert not (folder / ".env").exists()
    assert list((folder / "datasources").iterdir()) == []


def test_the_test_text_keeps_a_validation_message_readable_but_hides_the_value(project):
    from nl2sql_sdk import ConnectionCheck

    class Complaining:
        def test_connection(self):
            return ConnectionCheck(ok=False, stage="config", message=f"password: Field required (got {PASSWORD})",
                                   fix="Set connection.password")

    _, engine = project

    status = engine.datasources.test(Datasource(id="sales", adapter=Complaining(), connection={"password": PASSWORD}))

    assert status.reason == "password: Field required (got ***)"
    assert status.fix == "Set connection.password"


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
