"""``write_project``, ``starter_config`` and ``write_env_values`` (docs/architecture/wave_c.md, row L2, "Writing the config").

Temp folders only: nothing here reads or writes a real ``.env``.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from dotenv import dotenv_values

from nl2sql import SecretValueInConfig
from nl2sql.config import (LLM, AgentLLM, Config, Datasource, Embeddings, KnowledgeSourceConfig, Policies, Role,
                           SecretProviderConfig, Secrets, Storage, default_llm, load_config, starter_config,
                           write_env_values, write_project)

PASSWORD = "pw-literal-0001"
LLM_KEY = "sk-literal-llm-0002"
AGENT_KEY = "sk-literal-agent-0003"
EMBED_KEY = "sk-literal-embed-0004"
VAULT_TOKEN = "s.literal-vault-0005"
LITERALS = [PASSWORD, LLM_KEY, AGENT_KEY, EMBED_KEY, VAULT_TOKEN]


def _texts(folder: Path) -> dict:
    return {p.relative_to(folder).as_posix(): p.read_text(encoding="utf-8") for p in folder.rglob("*") if p.is_file()}


def _config(**changes) -> Config:
    base = Config(
        datasources=[Datasource(id="shop", type="sqlite", description="Shop", connection={"database": "shop.db"})],
        llm=LLM(provider="openai", model="gpt-test"),
        embeddings=Embeddings(provider="local"),
        policies=Policies(allow_all=True),
        storage=Storage(path=Path(".nl2sql")),
        knowledge=[KnowledgeSourceConfig(type="yaml", path="glossary.yaml")],
    )
    return base.model_copy(update=changes)


def _with_every_secret() -> Config:
    return _config(
        datasources=[Datasource(id="sales", type="postgres", connection={
            "host": "db.internal", "database": "sales", "user": "app", "password": PASSWORD})],
        llm=LLM(provider="openai", model="gpt-test", api_key=LLM_KEY,
                agents={"ast_planner": AgentLLM(api_key=AGENT_KEY)}),
        embeddings=Embeddings(provider="openai", api_key=EMBED_KEY),
        secrets=Secrets(providers=[SecretProviderConfig(type="hashi", id="vault", url="http://v", token=VAULT_TOKEN)]),
    )


# ---------------------------------------------------------------- the project

def test_write_project_round_trips_a_data_config(tmp_path):
    config = _config(
        datasources=[  # a folder loads in file-name order
            Datasource(id="sales", type="postgres", connection={
                "host": "db.internal", "database": "sales", "user": "app", "password": "${env:SALES_PW}"}),
            Datasource(id="shop", type="sqlite", description="Shop", connection={"database": "shop.db"},
                       sample_questions=["How many orders?"]),
        ],
        policies=Policies(roles={"admin": Role(description="Everything", allow=["*"])}),
        llm=LLM(provider="anthropic", model="claude-test", temperature=None),
        storage=Storage(path=tmp_path / "state"),
    )

    written = write_project(config, tmp_path)

    loaded = load_config(tmp_path / "nl2sql.yaml")
    assert loaded == config.model_copy(update={"storage": Storage(path=tmp_path / "state")})
    names = {p.relative_to(tmp_path).as_posix() for p in written}
    assert {"nl2sql.yaml", "datasources/shop.yaml", "datasources/sales.yaml", "llm.yaml", "embeddings.yaml",
            "policies.yaml", "storage.yaml", "knowledge.yaml", "glossary.yaml", ".gitignore"} <= names
    assert not (tmp_path / ".env").exists()


def test_write_project_writes_the_knowledge_section_and_an_empty_glossary_block_per_datasource(tmp_path):
    from nl2sql_sdk.knowledge import Glossary

    write_project(_config(), tmp_path)

    assert yaml.safe_load((tmp_path / "knowledge.yaml").read_text(encoding="utf-8")) == [
        {"type": "yaml", "path": "glossary.yaml"}]
    glossary = Glossary.model_validate(yaml.safe_load((tmp_path / "glossary.yaml").read_text(encoding="utf-8")))
    assert list(glossary.datasources) == ["shop"]


def test_write_project_writes_only_the_sections_that_are_set(tmp_path):
    write_project(_config(), tmp_path)

    toc = yaml.safe_load((tmp_path / "nl2sql.yaml").read_text(encoding="utf-8"))
    assert set(toc) == {"version", "datasources", "llm", "embeddings", "policies", "storage", "knowledge"}


def test_write_project_refuses_an_object_section_and_writes_nothing(tmp_path):
    config = _config(datasources=[Datasource(id="shop", adapter=object())])

    with pytest.raises(ValueError, match="datasources.*shop"):
        write_project(config, tmp_path)

    assert list(tmp_path.iterdir()) == []


def test_write_project_refuses_a_chat_model_object_naming_the_llm_section(tmp_path):
    with pytest.raises(ValueError, match="llm"):
        write_project(_config(llm=LLM(provider="openai", model=object())), tmp_path)

    assert list(tmp_path.iterdir()) == []


def test_write_project_refuses_existing_files_and_writes_nothing(tmp_path):
    (tmp_path / "llm.yaml").write_text("provider: mine\nmodel: mine\n", encoding="utf-8")

    with pytest.raises(FileExistsError, match="llm.yaml"):
        write_project(_config(), tmp_path)

    assert [p.name for p in tmp_path.iterdir()] == ["llm.yaml"]
    assert (tmp_path / "llm.yaml").read_text(encoding="utf-8") == "provider: mine\nmodel: mine\n"


def test_write_project_loads_what_it_wrote_before_it_returns(tmp_path, monkeypatch):
    from nl2sql.config import ConfigError

    monkeypatch.delenv("NL2SQL_TEST_UNSET", raising=False)
    config = _config(datasources=[Datasource(id="shop", type="sqlite", description="${env:NL2SQL_TEST_UNSET}",
                                             connection={"database": "shop.db"})])

    with pytest.raises(ConfigError, match="NL2SQL_TEST_UNSET"):
        write_project(config, tmp_path)


def test_extra_files_are_written_or_copied_and_checked_like_the_rest(tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("copied", encoding="utf-8")
    project = tmp_path / "project"

    write_project(_config(), project, extra={"data/shop.db": source, "notes.txt": "hello"})

    assert (project / "data" / "shop.db").read_text(encoding="utf-8") == "copied"
    assert (project / "notes.txt").read_text(encoding="utf-8") == "hello"
    with pytest.raises(FileExistsError, match="notes.txt"):
        write_project(_config(), project, extra={"notes.txt": "again"})


def test_a_storage_path_inside_the_folder_is_written_relative(tmp_path):
    write_project(_config(storage=Storage(path=tmp_path / "state")), tmp_path)

    assert yaml.safe_load((tmp_path / "storage.yaml").read_text(encoding="utf-8")) == {"path": "state"}
    assert "state/" in (tmp_path / ".gitignore").read_text(encoding="utf-8").splitlines()


# ---------------------------------------------------------------- the starter

def test_starter_config_has_no_datasources_and_a_yaml_glossary_beside_the_storage():
    config = starter_config(llm=default_llm("openai"), path=Path("proj") / ".nl2sql")

    assert config.datasources == []
    assert config.policies.allow_all is True
    assert config.embeddings.provider == "local"
    assert config.storage.path == Path("proj") / ".nl2sql"
    assert [(k.type, k.model_extra) for k in config.knowledge] == [("yaml", {"path": "proj/glossary.yaml"})]


def test_a_starter_project_is_written_with_its_glossary_and_loads(tmp_path):
    config = starter_config(llm=default_llm("openai"), path=tmp_path / ".nl2sql")

    write_project(config, tmp_path)

    loaded = load_config(tmp_path / "nl2sql.yaml")
    assert loaded.datasources == []
    assert loaded.knowledge[0].model_extra == {"path": "glossary.yaml"}
    assert (tmp_path / "datasources").is_dir() and (tmp_path / "glossary.yaml").is_file()


def test_an_engine_with_no_datasources_builds_and_checks(tmp_path, monkeypatch):
    from nl2sql import NL2SQL

    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-not-a-real-key")
    write_project(starter_config(llm=default_llm("openai"), path=tmp_path / ".nl2sql"), tmp_path)

    engine = NL2SQL.from_config(tmp_path / "nl2sql.yaml")
    try:
        assert engine.datasources.list() == []
        report = engine.check()
        assert report.datasources == [] and report.llm == {}
    finally:
        engine.close()


# ---------------------------------------------------------------- secrets

def test_every_secret_literal_goes_to_env_as_a_reference(tmp_path):
    write_project(_with_every_secret(), tmp_path, env_file=True)

    for name, text in _texts(tmp_path).items():
        if name != ".env":
            assert not any(literal in text for literal in LITERALS), name
    env = dotenv_values(tmp_path / ".env")
    assert env == {
        "NL2SQL_SALES_PASSWORD": PASSWORD,
        "NL2SQL_LLM_API_KEY": LLM_KEY,
        "NL2SQL_LLM_AGENTS_AST_PLANNER_API_KEY": AGENT_KEY,
        "NL2SQL_EMBEDDINGS_API_KEY": EMBED_KEY,
        "NL2SQL_SECRETS_VAULT_TOKEN": VAULT_TOKEN,
    }
    loaded = load_config(tmp_path / "nl2sql.yaml")
    assert loaded.datasources[0].connection["password"] == "${env:NL2SQL_SALES_PASSWORD}"
    assert loaded.llm.api_key == "${env:NL2SQL_LLM_API_KEY}"
    assert loaded.llm.agents["ast_planner"].api_key == "${env:NL2SQL_LLM_AGENTS_AST_PLANNER_API_KEY}"
    assert loaded.embeddings.api_key == "${env:NL2SQL_EMBEDDINGS_API_KEY}"
    assert (tmp_path / ".gitignore").read_text(encoding="utf-8").splitlines().count(".env") == 1


def test_a_reference_is_written_unchanged_and_needs_no_env_file(tmp_path):
    config = _config(llm=LLM(provider="openai", model="gpt-test", api_key="${env:MY_KEY}"),
                     secrets=Secrets(providers=[SecretProviderConfig(type="hashi", id="vault", url="http://v",
                                                                    token="${env:VAULT_TOKEN}")]),
                     datasources=[Datasource(id="s", type="postgres", connection={"password": "${vault:db/pw}"})])

    write_project(config, tmp_path)

    loaded = load_config(tmp_path / "nl2sql.yaml")
    assert loaded.llm.api_key == "${env:MY_KEY}"
    assert loaded.datasources[0].connection["password"] == "${vault:db/pw}"
    assert not (tmp_path / ".env").exists()


def test_secret_literal_is_refused_without_env_file(tmp_path):
    with pytest.raises(SecretValueInConfig) as raised:
        write_project(_with_every_secret(), tmp_path)

    message = str(raised.value)
    assert "password" in message and "${env:" in message
    assert not any(literal in message for literal in LITERALS)
    assert list(tmp_path.iterdir()) == []


def test_env_name_is_normalised_and_a_collision_is_refused(tmp_path):
    config = _config(datasources=[
        Datasource(id="my-db", type="postgres", connection={"password": "pw-one-1111"}),
        Datasource(id="my_db", type="postgres", connection={"password": "pw-two-2222"}),
    ])

    with pytest.raises(ValueError, match="my-db.*my_db|my_db.*my-db") as raised:
        write_project(config, tmp_path, env_file=True)

    assert "NL2SQL_MY_DB_PASSWORD" in str(raised.value)
    assert "pw-one-1111" not in str(raised.value) and "pw-two-2222" not in str(raised.value)
    assert list(tmp_path.iterdir()) == []


def test_a_name_is_normalised_from_the_field_path(tmp_path):
    config = _config(datasources=[Datasource(id="my-db.v2", type="postgres", connection={"password": "one"})])

    write_project(config, tmp_path, env_file=True)

    assert list(dotenv_values(tmp_path / ".env")) == ["NL2SQL_MY_DB_V2_PASSWORD"]


# ---------------------------------------------------------------- the env file

def test_env_file_keeps_other_lines(tmp_path):
    (tmp_path / ".env").write_text("# mine\nOPENAI_API_KEY=keep-me\nNL2SQL_A=old\n\nOTHER = spaced\n", encoding="utf-8")

    path = write_env_values(tmp_path, {"NL2SQL_A": "new", "NL2SQL_B": "added"})

    assert path == tmp_path / ".env"
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[:2] == ["# mine", "OPENAI_API_KEY=keep-me"]
    assert "NL2SQL_A=new" in lines and "NL2SQL_A=old" not in lines
    assert lines.count("OTHER = spaced") == 1 and "" in lines
    assert dotenv_values(path) == {"OPENAI_API_KEY": "keep-me", "NL2SQL_A": "new", "OTHER": "spaced",
                                   "NL2SQL_B": "added"}


def test_env_file_is_created_and_gitignored(tmp_path):
    write_env_values(tmp_path, {"NL2SQL_A": "1"})

    assert (tmp_path / ".env").read_text(encoding="utf-8") == "NL2SQL_A=1\n"
    assert ".env" in (tmp_path / ".gitignore").read_text(encoding="utf-8").splitlines()


def test_env_file_writes_a_value_dotenv_reads_back(tmp_path):
    values = {"NL2SQL_A": "has space", "NL2SQL_B": "p#ss'word\"$x\\y", "NL2SQL_C": "", "NL2SQL_D": "plain-._/:@+="}

    write_env_values(tmp_path, values)

    assert dotenv_values(tmp_path / ".env") == values


@pytest.mark.parametrize("name, value", [("bad name", "x"), ("1BAD", "x"), ("NL2SQL_A", "two\nlines")])
def test_env_file_refuses_a_bad_name_or_value_and_writes_nothing(tmp_path, name, value):
    with pytest.raises(ValueError):
        write_env_values(tmp_path, {name: value})

    assert not (tmp_path / ".env").exists()


LINE_BREAKS = ["\n", "\r", "\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x85", " ", " "]


@pytest.mark.parametrize("character", LINE_BREAKS)
def test_env_file_refuses_every_character_splitlines_splits_on(tmp_path, character):
    for value in (f"a{character}OPENAI_BASE_URL=http://evil", f"abc{character}", character):
        with pytest.raises(ValueError, match="spans lines"):
            write_env_values(tmp_path, {"NL2SQL_A": value})

    assert not (tmp_path / ".env").exists()


def test_env_file_does_not_split_a_kept_line_on_a_unicode_separator(tmp_path):
    (tmp_path / ".env").write_text("KEEP=a OPENAI_BASE_URL=x\n", encoding="utf-8")

    write_env_values(tmp_path, {"NL2SQL_A": "1"})

    assert (tmp_path / ".env").read_text(encoding="utf-8") == "KEEP=a OPENAI_BASE_URL=x\nNL2SQL_A=1\n"


@pytest.mark.parametrize("datasource_id", ["a:b", "a:b.yaml", "con", "CON", "Nul", "aux", "prn", "com1", "LPT9",
                                           "con.v2", "a.", "a ", "-lead", ".hidden", "a b", "a*b", "a?b", "a\x00b", "é", "x" * 129])
def test_check_file_name_refuses_what_is_not_on_the_allow_list(datasource_id):
    from nl2sql.config.writer import check_file_name

    with pytest.raises(ValueError, match="file name"):
        check_file_name(datasource_id)


@pytest.mark.parametrize("datasource_id", ["sales", "my-db", "my_db", "Sales2", "a.b", "com10", "console"])
def test_check_file_name_accepts_a_plain_id(datasource_id):
    from nl2sql.config.writer import check_file_name

    assert check_file_name(datasource_id) == f"{datasource_id}.yaml"


def test_a_datasource_named_like_the_llm_cannot_take_its_env_name(tmp_path):
    config = _config(
        datasources=[Datasource(id="llm", type="postgres", connection={"api_key": "ds-key-1111"})],
        llm=LLM(provider="openai", model="gpt-test", api_key=LLM_KEY))

    with pytest.raises(ValueError, match="NL2SQL_LLM_API_KEY") as raised:
        write_project(config, tmp_path, env_file=True)

    assert "ds-key-1111" not in str(raised.value) and LLM_KEY not in str(raised.value)
    assert list(tmp_path.iterdir()) == []


def test_config_reserved_names_cover_every_non_datasource_secret():
    from nl2sql.config.writer import config_reserved_names

    config = _with_every_secret().model_copy(update={"llm": LLM(
        provider="openai", model="m", api_key="${env:K}", headers={"X-Org": "o"},
        agents={"ast_planner": AgentLLM(api_key="${env:A}", headers={"X-Agent": "a"})})})

    suffixes = [
        "LLM_API_KEY", "LLM_HEADERS_X_ORG", "LLM_AGENTS_AST_PLANNER_API_KEY",
        "LLM_AGENTS_AST_PLANNER_HEADERS_X_AGENT", "EMBEDDINGS_API_KEY", "SECRETS_VAULT_TOKEN"]
    assert set(config_reserved_names(config)) == {"NL2SQL_" + s for s in suffixes}


def test_init_project_demo_keeps_the_settings_comment(tmp_path):
    from nl2sql.config import init_project

    init_project(tmp_path, demo=True)

    text = (tmp_path / "settings.yaml").read_text(encoding="utf-8")
    assert "six model calls" in text and "300 s" in text and "Debug drill-down" in text


def test_the_env_writer_never_touches_the_process_environment(tmp_path, monkeypatch):
    import os

    monkeypatch.delenv("NL2SQL_Z", raising=False)
    before = dict(os.environ)

    write_env_values(tmp_path, {"NL2SQL_Z": "value"})

    assert dict(os.environ) == before
