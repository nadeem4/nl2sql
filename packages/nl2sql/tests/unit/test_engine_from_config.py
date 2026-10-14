"""The engine built from a ``Config``: construction, ``check()``, ``close()`` and the ways to create one.

docs/architecture/configuration_v2.md, section 4 ("Creating the engine",
"Lifecycle") and the PR 7a row of section 11.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from langchain_core.embeddings import DeterministicFakeEmbedding
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from nl2sql import (
    LLM,
    CheckReport,
    Config,
    ConfigError,
    Datasource,
    Embeddings,
    EngineNotReady,
    NL2SQL,
    Policies,
    Registry,
    Storage,
    load_config,
)
from nl2sql.testing.config import make_config
from nl2sql.testing import OpenConnection
from nl2sql_sdk import ConnectionCheck

KEY_VARIABLES = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY")


def _shop(path: Path) -> Path:
    """A small SQLite database at ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT)")
        connection.execute("INSERT INTO customers VALUES (1, 'Ada')")
    return path


def _fake_embeddings() -> dict:
    return {"instance": DeterministicFakeEmbedding(size=8)}


def _engine(tmp_path: Path, **sections) -> NL2SQL:
    """An engine on a ``make_config`` folder, embedding with a fake so nothing is downloaded."""
    return NL2SQL(make_config(tmp_path, **sections).model_copy(
        update={"embeddings": Embeddings(**_fake_embeddings())}))


class CountingAdapter:
    """An adapter that counts every call that would reach a database."""

    interface_version = 2
    title = "Stub"
    connection_model = OpenConnection
    driver_modules = ()
    install_hint = ""
    instances: list = []

    def __init__(self, datasource_id=None, connection=None, limits=None, *, folder=None):
        self.datasource_id = datasource_id
        self.connection_args = connection.model_dump() if connection is not None else {}
        self.calls = []
        self.disposed = 0
        self.healthy = self.connection_args.get("healthy", True)
        CountingAdapter.instances.append(self)

    def capabilities(self):
        return set()

    def test_connection(self):
        self.calls.append("test_connection")
        return ConnectionCheck(ok=self.healthy, message=None if self.healthy else "refused")

    def fetch_schema_snapshot(self, scope, on_table=None):
        self.calls.append("fetch_schema_snapshot")
        raise AssertionError("not reached")

    def execute(self, request):
        self.calls.append("execute")
        raise AssertionError("not reached")

    def get_dialect(self):
        return "sqlite"

    def close(self):
        self.disposed += 1


@pytest.fixture
def counting():
    CountingAdapter.instances = []
    registry = Registry.default()
    registry.add_adapter("counting", CountingAdapter)
    return registry


# ---------------------------------------------------------------- construction

def test_constructor_never_connects(tmp_path, counting):
    database = tmp_path / "shop.db"
    cfg = make_config(tmp_path, datasources=[
        {"id": "shop", "type": "sqlite", "connection": {"database": database.as_posix()}},
        {"id": "remote", "type": "counting", "connection": {"host": "db.invalid"}},
    ])

    engine = NL2SQL(cfg.model_copy(update={"embeddings": Embeddings(**_fake_embeddings())}), registry=counting)

    # SQLite creates its file on the first connection; nothing has made one.
    assert not database.exists()
    assert CountingAdapter.instances and all(a.calls == [] for a in CountingAdapter.instances)
    assert sorted(d.id for d in engine.datasources.list()) == ["remote", "shop"]


def test_constructor_creates_the_stores_under_storage_path(tmp_path):
    engine = _engine(tmp_path)
    storage = tmp_path / ".nl2sql"

    assert engine.config.storage.path == storage
    # The index store opens its folder on the first build, not before.
    assert not (storage / "index").exists()
    assert (storage / "state.db").is_file()
    assert engine._runtime.artifact_store.results.directory == storage / "results"
    assert engine._runtime.traces.directory == storage / "traces"


def test_registry_load_errors_are_gathered_into_one_config_error(tmp_path):
    cfg = make_config(tmp_path, datasources=[
        {"id": "a", "type": "sqllite", "connection": {"database": "a.db"}},
        {"id": "b", "type": "nosuchdb", "connection": {}},
    ], llm={"provider": "antropic", "model": "claude-sonnet-5-5"})

    with pytest.raises(ConfigError) as caught:
        NL2SQL(cfg)

    text = str(caught.value)
    assert len(caught.value.problems) == 3
    assert "datasources[0].type" in text and "Did you mean 'sqlite'?" in text
    assert "datasources[1].type" in text
    assert "llm.provider" in text and "Did you mean 'anthropic'?" in text


def test_secret_provider_failure_fails_startup(tmp_path):
    class Broken:
        interface_version = 1

        from pydantic import BaseModel, ConfigDict

        class config_model(BaseModel):
            model_config = {"extra": "forbid"}
            url: str

        @classmethod
        def from_config(cls, cfg):
            raise RuntimeError("the vault is sealed")

        def get_secret(self, key):
            return None

    registry = Registry.default()
    registry.add_secrets("broken", Broken)
    cfg = make_config(tmp_path, secrets={"providers": [{"id": "vault", "type": "broken", "url": "https://v"}]})

    with pytest.raises(ConfigError) as caught:
        NL2SQL(cfg, registry=registry)

    assert "secrets.providers[0]" in str(caught.value)
    assert "the vault is sealed" in str(caught.value)


def test_secret_provider_block_is_checked_by_its_config_model(tmp_path):
    cfg = make_config(tmp_path, secrets={"providers": [
        {"id": "vault", "type": "hashicorp", "url": "https://vault", "tokn": "x"}]})

    with pytest.raises(ConfigError) as caught:
        NL2SQL(cfg)

    assert "secrets.providers[0]" in str(caught.value) and "tokn" in str(caught.value)


def test_a_datasource_secret_is_resolved_by_the_engines_own_secret_manager(tmp_path, monkeypatch):
    monkeypatch.setenv("SHOP_DB", _shop(tmp_path / "shop.db").as_posix())
    engine = _engine(tmp_path, datasources=[
        {"id": "shop", "type": "sqlite", "connection": {"database": "${env:SHOP_DB}"}}])

    assert engine.check().datasources[0].status == "ready"
    assert engine.config.datasources[0].connection["database"] == "${env:SHOP_DB}"


# ---------------------------------------------------------------- check()

def test_check_reports_failed_datasource_and_runs_degraded(tmp_path):
    _shop(tmp_path / "shop.db")
    engine = _engine(tmp_path, datasources=[
        {"id": "shop", "type": "sqlite", "connection": {"database": "shop.db"}},
        {"id": "broken", "type": "sqlite", "connection": {"database": "no/such/folder/x.db"}},
    ])

    report = engine.check()

    assert isinstance(report, CheckReport)
    assert not report.ok
    status = {d.id: d for d in report.datasources}
    assert status["shop"].status == "ready"
    assert status["broken"].status == "failed"
    assert status["broken"].stage == "database" and status["broken"].reason and status["broken"].fix
    assert "broken" in str(report)
    # Left out of routing, and a query that targets it gets the reason.
    assert [d.id for d in engine.datasources.list() if d.status == "ready"] == ["shop"]
    assert engine.datasources.get("broken").status == "failed"
    # Safe to call again.
    assert [d.status for d in engine.check().datasources] == ["ready", "failed"]


def test_required_datasource_failure_raises_in_check(tmp_path):
    _shop(tmp_path / "shop.db")
    engine = _engine(tmp_path, datasources=[
        {"id": "shop", "type": "sqlite", "connection": {"database": "shop.db"}},
        {"id": "ledger", "type": "sqlite", "required": True,
         "connection": {"database": "no/such/folder/x.db"}},
    ])

    with pytest.raises(EngineNotReady) as caught:
        engine.check()

    assert "ledger" in str(caught.value)
    assert caught.value.report.datasources[1].status == "failed"


def test_check_raises_when_datasources_are_configured_and_none_is_ready(tmp_path):
    engine = _engine(tmp_path, datasources=[
        {"id": "broken", "type": "sqlite", "connection": {"database": "no/such/folder/x.db"}}])

    with pytest.raises(EngineNotReady):
        engine.check()


def test_check_reports_an_agent_whose_key_does_not_resolve(tmp_path, monkeypatch):
    for variable in KEY_VARIABLES:
        monkeypatch.delenv(variable, raising=False)
    engine = _engine(tmp_path)

    report = engine.check()

    assert "OPENAI_API_KEY" in report.llm["default"]
    assert not report.ok


def test_check_passes_with_keys_and_ready_datasources_and_reports_the_index(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-check-test-0000")
    _shop(tmp_path / "shop.db")
    engine = _engine(tmp_path, datasources=[
        {"id": "shop", "type": "sqlite", "connection": {"database": "shop.db"}}])

    report = engine.check()

    assert report.llm == {}
    assert report.index in {"empty", "missing"}


# ---------------------------------------------------------------- close()

def test_close_disposes_pools_and_is_idempotent(tmp_path, counting):
    _shop(tmp_path / "shop.db")
    cfg = make_config(tmp_path, datasources=[
        {"id": "shop", "type": "sqlite", "connection": {"database": "shop.db"}},
        {"id": "remote", "type": "counting", "connection": {}},
    ]).model_copy(update={"embeddings": Embeddings(**_fake_embeddings())})
    engine = NL2SQL(cfg, registry=counting)
    engine.check()
    sqlite_adapter = engine._runtime.ds_registry.get_adapter("shop")
    disposed = []
    original = sqlite_adapter.engine.dispose
    sqlite_adapter.engine.dispose = lambda *a, **k: (disposed.append(1), original(*a, **k))

    engine.close()
    engine.close()

    assert disposed == [1]
    assert CountingAdapter.instances[0].disposed == 1
    with pytest.raises(RuntimeError, match="closed"):
        engine.run_query("How many customers are there?")


def test_the_context_manager_closes_the_engine(tmp_path):
    with _engine(tmp_path) as engine:
        assert not engine.closed
    assert engine.closed


# ---------------------------------------------------------------- datasources

def test_datasource_added_in_code_keeps_its_description(tmp_path):
    engine = _engine(tmp_path)
    engine.datasources.add(Datasource(id="shop", type="sqlite", description="Customers of the shop.",
                                     sample_questions=["How many customers are there?"],
                                     connection={"database": _shop(tmp_path / "shop.db").as_posix()}))

    assert engine.index.build(["shop"]).ok

    snapshot = engine._runtime.vector_store.snapshot("shop")
    assert snapshot.metadata.description == "Customers of the shop."
    assert [d.id for d in engine.config.datasources] == ["shop"]
    assert engine._runtime.ds_registry.get_datasource("shop").sample_questions == [
        "How many customers are there?"]


def test_sample_questions_are_read_from_the_datasource(tmp_path):
    _shop(tmp_path / "shop.db")
    engine = _engine(tmp_path, datasources=[
        {"id": "shop", "type": "sqlite", "connection": {"database": "shop.db"},
         "sample_questions": ["Who are our customers?"]}])

    assert engine._runtime.ds_registry.get_datasource("shop").sample_questions == ["Who are our customers?"]


def test_relative_sqlite_path_is_resolved_against_the_config_folder(tmp_path, monkeypatch):
    project = tmp_path / "project"
    _shop(project / "data" / "shop.db")
    engine = _engine(project, datasources=[
        {"id": "shop", "type": "sqlite", "connection": {"database": "data/shop.db"}}])
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    assert engine.check().datasources[0].status == "ready"
    assert not (elsewhere / "data").exists()


def test_relative_sqlite_path_in_code_is_relative_to_the_working_directory(tmp_path, monkeypatch):
    _shop(tmp_path / "shop.db")
    monkeypatch.chdir(tmp_path)
    engine = NL2SQL(Config(
        datasources=[Datasource(id="shop", type="sqlite", connection={"database": "shop.db"})],
        llm=LLM(provider="openai", model="gpt-test"),
        embeddings=Embeddings(**_fake_embeddings()),
        policies=Policies.allow_all(),
        storage=Storage(path=".nl2sql"),
    ))

    assert engine.check().datasources[0].status == "ready"


# ---------------------------------------------------------------- per engine

def test_embedder_is_per_engine(tmp_path):
    a = NL2SQL(make_config(tmp_path / "a"))
    b = NL2SQL(make_config(tmp_path / "b"))
    mine = DeterministicFakeEmbedding(size=8)
    c = NL2SQL(make_config(tmp_path / "c").model_copy(update={"embeddings": Embeddings(instance=mine)}))

    assert a._runtime.embedder is not b._runtime.embedder
    assert c._runtime.embedder is mine
    assert c._runtime.vector_store.embeddings is mine


def test_secret_manager_is_per_engine(tmp_path):
    a = _engine(tmp_path / "a")
    b = _engine(tmp_path / "b")

    assert a._runtime.secret_manager is not b._runtime.secret_manager


def test_allow_all_policies_let_every_caller_read_everything(tmp_path):
    from nl2sql import UserContext

    engine = _engine(tmp_path)

    assert engine.policies.allows(UserContext(roles=[]), "shop", "customers", "main")


# ---------------------------------------------------------------- the six ways

def test_1_from_the_file(tmp_path, monkeypatch):
    cfg = make_config(tmp_path, embeddings={"provider": "local"})

    assert NL2SQL.from_config(tmp_path / "nl2sql.yaml").config == cfg
    monkeypatch.setenv("NL2SQL_CONFIG", str(tmp_path / "nl2sql.yaml"))
    assert NL2SQL.from_config().config == cfg


def test_2_from_the_file_with_your_own_components(tmp_path, counting):
    make_config(tmp_path, datasources=[{"id": "dw", "type": "counting", "connection": {}}])

    engine = NL2SQL.from_config(tmp_path / "nl2sql.yaml", registry=counting)
    assert [d.id for d in engine.datasources.list()] == ["dw"]

    with pytest.raises(ConfigError, match="Unknown adapter 'counting'"):
        NL2SQL.from_config(tmp_path / "nl2sql.yaml")


def test_3_in_code_by_name(tmp_path):
    database = _shop(tmp_path / "shop.db")

    engine = NL2SQL(Config(
        datasources=[Datasource(id="shop", type="sqlite", connection={"database": database.as_posix()})],
        llm=LLM(provider="anthropic", model="claude-sonnet-5-5"),
        embeddings=Embeddings(**_fake_embeddings()),
        policies=Policies.allow_all(),
        storage=Storage(path=tmp_path / ".nl2sql"),
    ))

    assert [d.id for d in engine.datasources.list()] == ["shop"]
    assert engine.llm.get("default")["provider"] == "anthropic"


def test_4_in_code_passing_objects(tmp_path):
    from nl2sql.storage.chroma import ChromaIndex
    from nl2sql.storage.files import FilesTraces
    from nl2sql.storage.local import LocalResults
    from nl2sql.storage.sqlite import SqliteState

    adapter = CountingAdapter(datasource_id="dw")
    model = FakeListChatModel(responses=["ok"])
    folder = tmp_path / ".nl2sql"

    engine = NL2SQL(Config(
        datasources=[Datasource(id="dw", adapter=adapter)],
        llm=LLM(model=model),
        embeddings=Embeddings(**_fake_embeddings()),
        policies=Policies.allow_all(),
        # An empty registry names no store either, so each slot gets its object.
        storage=Storage(path=folder, index=ChromaIndex(folder / "index"), state=SqliteState(folder / "state.db"),
                        traces=FilesTraces(folder / "traces"), results=LocalResults(folder / "results")),
    ), registry=Registry.empty())

    assert engine._runtime.ds_registry.get_adapter("dw") is adapter
    assert engine._runtime.llm_registry.get_llm("astplanner") is model


def test_5_mixed_load_the_file_then_change_it_in_code(tmp_path):
    make_config(tmp_path)
    database = _shop(tmp_path / "shop.db")

    cfg = load_config(tmp_path / "nl2sql.yaml")
    cfg = cfg.with_datasource(Datasource(id="shop", type="sqlite", connection={"database": database.as_posix()}))
    engine = NL2SQL(cfg.model_copy(update={"embeddings": Embeddings(**_fake_embeddings())}))

    assert [d.id for d in engine.datasources.list()] == ["shop"]


def test_demo_builds_from_bundled_files(tmp_path, monkeypatch):
    from nl2sql.datasets import CHINOOK_DB_PATH

    for variable in KEY_VARIABLES:
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-demo-test-0000")

    engine = NL2SQL.demo(path=tmp_path / "demo")

    assert sorted(d.id for d in engine.datasources.list()) == ["chinook", "support", "webanalytics"]
    databases = {Path(d.connection["database"]).parent for d in engine.config.datasources}
    assert databases == {CHINOOK_DB_PATH.parent}
    assert engine.config.llm.provider == "anthropic"
    assert engine.config.storage.path == tmp_path / "demo"
    assert engine.config.curated is not None
    assert sorted(engine.config.policies.roles) == ["admin", "analyst", "viewer"]


def test_demo_takes_the_llm_it_is_given(tmp_path):
    llm = LLM(provider="openai", model="gpt-5.4")

    assert NL2SQL.demo(llm=llm, path=tmp_path / "demo").config.llm == llm


def test_demo_without_any_key_names_the_variables(tmp_path, monkeypatch):
    for variable in KEY_VARIABLES:
        monkeypatch.delenv(variable, raising=False)

    with pytest.raises(ConfigError) as caught:
        NL2SQL.demo(path=tmp_path / "demo")

    assert all(variable in str(caught.value) for variable in KEY_VARIABLES)


# ---------------------------------------------------------------- exports

def test_the_config_classes_are_exported_from_nl2sql():
    import nl2sql
    import nl2sql.config as config

    for name in ("Config", "load_config", "ConfigError", "Datasource", "Limits", "LLM", "AgentLLM",
                 "Embeddings", "Policies", "Role", "Storage", "Secrets", "Curated", "Settings"):
        assert getattr(nl2sql, name) is getattr(config, name)
    assert nl2sql.CheckReport is CheckReport and nl2sql.EngineNotReady is EngineNotReady
