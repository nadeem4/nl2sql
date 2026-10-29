"""``nl2sql launch`` (docs/architecture/wave_c.md, section 7; row L3).

A temp folder holds a SQLite file. Indexing is replaced by a build that writes the snapshot and the pointer without
embedding (as ``test_cli_profile`` does), so no model is loaded; a fake LLM server stands behind ``OPENAI_BASE_URL``
and records every call.
"""
from __future__ import annotations

import os
import re
import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
import yaml
from nl2sql_sdk.discovery import Found
from nl2sql_sdk.stores import BuildPointer
from typer.testing import CliRunner

from nl2sql import Candidate, NL2SQL, UserContext, load_config
from nl2sql.cli.main import app
from nl2sql.config import default_llm, starter_config, write_project
from nl2sql.testing.fake_llm import FakeLLMServer

runner = CliRunner()
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
KEY = "sk-launch-test-key-0123456789"
PASSWORD = "p4ssw0rd-launch-test"
ADMIN = UserContext(user_id=None, roles=[])


def _out(result) -> str:
    return re.sub(r"\s+", " ", ANSI.sub("", result.output))


def _database(folder, name="shop.db"):
    with sqlite3.connect(folder / name) as connection:
        connection.executescript("""
            CREATE TABLE customers (customer_id INTEGER PRIMARY KEY, name TEXT, country TEXT);
            CREATE TABLE orders (order_id INTEGER PRIMARY KEY, customer_id INTEGER, total NUMERIC, placed DATE);
            INSERT INTO customers VALUES (1, 'a', 'FR'), (2, 'b', 'DE');
            INSERT INTO orders VALUES (1, 1, 5.0, '2024-01-02'), (2, 2, 7.5, '2024-02-03');
        """)
    connection.close()


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    for name in ("OPENAI_API_KEY", "DATABASE_URL", "NL2SQL_LLM_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    snapshot = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(snapshot)


@pytest.fixture
def llm_server(monkeypatch):
    fake = FakeLLMServer([]).start()
    monkeypatch.setenv("OPENAI_BASE_URL", fake.base_url)
    yield fake
    fake.stop()


@pytest.fixture
def index(monkeypatch):
    """Replaces ``engine.index.build``: the snapshot and the pointer, and what the environment held at that moment."""
    seen = SimpleNamespace(calls=[], key=None)

    def build(self, datasource_ids=None, *, on_progress=None, on_datasource=None):
        ctx = self._ctx
        seen.calls.append(list(datasource_ids))
        seen.key = os.environ.get("NL2SQL_LLM_API_KEY")
        for datasource_id in datasource_ids:
            adapter = ctx.ds_registry.get_adapter(datasource_id)
            version = ctx.state.put_snapshot(adapter.fetch_schema_snapshot(ctx.ds_registry.get_index_scope(datasource_id)))
            ctx.index.open(ctx.namespace, 384)
            ctx.index.set_pointers(ctx.namespace, {datasource_id: BuildPointer(
                build_id="b1", schema_version=version, built_at=datetime.now(timezone.utc))})
        return SimpleNamespace(ok=True, stats={}, empty=False, errors=[])

    monkeypatch.setattr("nl2sql.api.indexing_api.IndexAPI.build", build)
    return seen


@pytest.fixture
def folder(tmp_path):
    _database(tmp_path)
    return tmp_path


def _launch(folder, *args, input=""):
    return runner.invoke(app, ["launch", str(folder), "--no-ports", *args], input=input)


# A new project: pick the file, accept the datasource's name, type the key; then --yes skips the review.
NEW_PROJECT = "1\n\n" + KEY + "\n"


def _yaml_text(folder) -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(folder.rglob("*.yaml")) if ".nl2sql" not in p.parts)


def _engine(folder) -> NL2SQL:
    return NL2SQL(load_config(folder / "nl2sql.yaml"))


def test_launch_finds_the_file_and_writes_a_project(folder, index, llm_server):
    result = _launch(folder, "--yes", input=NEW_PROJECT)

    assert result.exit_code == 0, _out(result)
    assert "shop.db" in _out(result)
    config = load_config(folder / "nl2sql.yaml")
    assert [d.id for d in config.datasources] == ["shop"]
    assert (folder / "datasources" / "shop.yaml").is_file() and (folder / "glossary.yaml").is_file()
    assert index.calls == [["shop"]]
    assert 'nl2sql --config' in _out(result) and "demo --dir" in _out(result)


def test_launch_into_an_existing_project_saves_one_datasource(folder, index, llm_server, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    write_project(starter_config(llm=default_llm("openai"), path=folder / ".nl2sql"), folder)
    before = {p.name: p.read_bytes() for p in folder.glob("*.yaml")}

    result = _launch(folder, "--yes", input="1\n\n")

    assert result.exit_code == 0, _out(result)
    assert sorted(p.name for p in (folder / "datasources").iterdir()) == ["shop.yaml"]
    assert {name: (folder / name).read_bytes() for name in before} == before
    assert [d.id for d in load_config(folder / "nl2sql.yaml").datasources] == ["shop"]
    assert not (folder / ".env").exists()  # the key was in the environment, a file connection has no secret


def test_failed_test_shows_the_stage_and_asks_again(folder, index, llm_server):
    # Candidate 1 is shop.db, 2 is "another database"; its form asks for the file twice: a text file, then shop.db.
    (folder / "notes.txt").write_text("not a database", encoding="utf-8")
    wrong = str(folder / "notes.txt")
    result = _launch(folder, "--yes", input="2\nsqlite\n" + wrong + "\nshop\n" + str(folder / "shop.db") + "\n" + KEY + "\n")

    out = _out(result)
    assert result.exit_code == 0, out
    assert "failed" in out.lower() and "Fix:" in out
    assert [d.id for d in load_config(folder / "nl2sql.yaml").datasources] == ["shop"]


def test_key_is_written_to_env_never_yaml(folder, index, llm_server):
    result = _launch(folder, "--yes", input=NEW_PROJECT)

    assert result.exit_code == 0, _out(result)
    env = (folder / ".env").read_text(encoding="utf-8")
    assert f"NL2SQL_LLM_API_KEY={KEY}" in env
    assert KEY not in _yaml_text(folder) and "${env:NL2SQL_LLM_API_KEY}" in _yaml_text(folder)
    assert KEY not in result.output
    assert ".env" in (folder / ".gitignore").read_text(encoding="utf-8")


def test_launch_indexes_with_the_secret_it_wrote_to_env(folder, index, llm_server):
    result = _launch(folder, "--yes", input=NEW_PROJECT)

    assert result.exit_code == 0, _out(result)
    assert index.key == KEY  # load_env_file ran, and the engine was rebuilt, before the index


def test_a_failed_test_never_prints_the_password(folder, index, llm_server, monkeypatch):
    candidate = Candidate(type="sqlite", title="SQLite", found=Found(kind="url", value="sqlite://u:***@h/x"),
                          connection={"database": str(folder / "no" / "such.db"), "password": PASSWORD},
                          missing=[], installed=True, install_hint="")
    monkeypatch.setattr("nl2sql.api.datasource_api.DatasourcesAPI.scan", lambda self, *a, **k: [candidate])

    result = _launch(folder, input="1\n\n")

    assert "failed" in _out(result).lower() and PASSWORD not in result.output
    assert not (folder / "nl2sql.yaml").exists()

def test_starter_questions_are_example_proposals_not_sample_questions(folder, index, llm_server):
    result = _launch(folder, "--yes", input=NEW_PROJECT)

    assert result.exit_code == 0, _out(result)
    with _engine(folder) as engine:
        examples = engine.proposals.list(user_context=ADMIN, kind="example")
    assert examples and all(p.origin == "template" and p.status == "proposed" for p in examples)
    for proposal in examples:
        assert proposal.target in _out(result)
    assert "sample_questions" not in _yaml_text(folder)
    assert yaml.safe_load((folder / "glossary.yaml").read_text(encoding="utf-8")).get("datasources", {}).get(
        "shop", {}).get("examples", []) == []


def test_yes_exports_the_review_file_and_accepts_nothing(folder, index, llm_server):
    result = _launch(folder, "--yes", input=NEW_PROJECT)

    assert result.exit_code == 0, _out(result)
    review = yaml.safe_load((folder / "review.yaml").read_text(encoding="utf-8"))
    with _engine(folder) as engine:
        proposals = engine.proposals.list(user_context=ADMIN, status=None)
    assert {item["id"] for item in review["proposals"]} == {p.id for p in proposals}
    assert proposals and all(p.status == "proposed" for p in proposals)
    assert all(item["decision"] is None for item in review["proposals"])


def test_review_rejects_what_the_person_rejects(folder, index, llm_server):
    result = _launch(folder, input=NEW_PROJECT + "r\n" * 40)

    assert result.exit_code == 0, _out(result)
    with _engine(folder) as engine:
        assert engine.proposals.list(user_context=ADMIN) == []
        assert engine.proposals.list(user_context=ADMIN, status="rejected")
    assert not (folder / "review.yaml").exists()


def test_review_accepts_a_starter_question_into_the_glossary(folder, index, llm_server):
    result = _launch(folder, input=NEW_PROJECT + "a\n" * 40)

    assert result.exit_code == 0, _out(result)
    glossary = yaml.safe_load((folder / "glossary.yaml").read_text(encoding="utf-8"))
    assert glossary["datasources"]["shop"]["examples"]

def test_missing_driver_prints_the_pip_and_uvx_fixes(folder, index, llm_server, monkeypatch):
    candidate = Candidate(type="postgres", title="PostgreSQL", found=Found(kind="port", value="127.0.0.1:5432"),
                          connection={"host": "127.0.0.1", "port": 5432}, missing=["database", "username"],
                          installed=False, install_hint='pip install "nl2sql-engine[postgres]"')
    monkeypatch.setattr("nl2sql.api.datasource_api.DatasourcesAPI.scan", lambda self, *a, **k: [candidate])

    result = _launch(folder, input="1\n")

    out = _out(result)
    assert result.exit_code == 1
    assert 'pip install "nl2sql-engine[postgres]"' in out
    assert 'uvx --from "nl2sql-engine[postgres]" nl2sql launch' in out
    assert not (folder / "nl2sql.yaml").exists()


def test_launch_makes_no_model_call_without_draft(folder, index, llm_server):
    result = _launch(folder, "--yes", input=NEW_PROJECT)

    assert result.exit_code == 0, _out(result)
    assert llm_server.calls == []


def test_an_unknown_provider_is_refused_before_anything_is_written(folder, index, llm_server):
    result = _launch(folder, "--provider", "nope", input="1\n")

    assert result.exit_code == 1 and "nope" in _out(result)
    assert not (folder / "nl2sql.yaml").exists()


def test_an_existing_file_is_never_overwritten(folder, index, llm_server):
    (folder / "glossary.yaml").write_text("mine: true\n", encoding="utf-8")

    result = _launch(folder, "--yes", input=NEW_PROJECT)

    assert result.exit_code == 1 and "glossary.yaml" in _out(result)
    assert (folder / "glossary.yaml").read_text(encoding="utf-8") == "mine: true\n"
    assert not (folder / "nl2sql.yaml").exists()


def test_demo_serves_a_launched_folder_without_a_warning(folder, index, llm_server, capsys):
    from nl2sql.cli.commands.demo import prepare_project
    from nl2sql.cli.demo.stamp import outdated_warning

    assert _launch(folder, "--yes", input=NEW_PROJECT).exit_code == 0
    capsys.readouterr()

    assert prepare_project(folder) == folder / "nl2sql.yaml"

    assert outdated_warning(folder) is None
    assert "created by" not in capsys.readouterr().out


def test_demo_still_warns_for_a_stamp_from_an_older_version(tmp_path):
    from nl2sql.cli.demo.stamp import STAMP_FILE, outdated_warning

    (tmp_path / STAMP_FILE).write_text('{"engine_version": "0.0.1"}', encoding="utf-8")

    assert "nl2sql-engine 0.0.1" in outdated_warning(tmp_path)