"""``load_config``: one test per rule in section 2 of configuration_v2.md.

The file layout, ``file:`` includes, ``${provider:key}`` interpolation, strict
keys with "did you mean", and the error report. The section models themselves
are covered in ``test_config_models.py``.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from nl2sql.config import Config, ConfigError, load_config

MINIMAL = """\
version: 2
datasources: []
llm:        { provider: anthropic, model: claude-sonnet-5-5 }
embeddings: { provider: local }
policies:   { allow_all: true }
storage:    { path: .nl2sql }
"""


def write(folder: Path, name: str, text: str) -> Path:
    path = folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


def problems_of(error: ConfigError):
    return [(p.file, p.path, p.message) for p in error.problems]


def load_error(path: Path) -> ConfigError:
    with pytest.raises(ConfigError) as caught:
        load_config(path)
    return caught.value


# ---------------------------------------------------------------- the table of contents

def test_inline_and_split_layouts_build_the_same_config(tmp_path):
    inline = write(tmp_path / "inline", "nl2sql.yaml", """\
        version: 2
        datasources:
          - id: shop
            type: sqlite
            connection: { database: shop.db }
        llm:        { provider: anthropic, model: claude-sonnet-5-5 }
        embeddings: { provider: local }
        policies:   { allow_all: true }
        storage:    { path: .nl2sql }
        """)
    split = tmp_path / "split"
    write(split, "nl2sql.yaml", """\
        version: 2
        datasources: { file: datasources/ }
        llm:         { file: llm.yaml }
        embeddings:  { file: embeddings.yaml }
        policies:    { file: policies.yaml }
        storage:     { file: storage.yaml }
        """)
    write(split, "datasources/shop.yaml", "type: sqlite\nconnection: { database: shop.db }\n")
    write(split, "llm.yaml", "provider: anthropic\nmodel: claude-sonnet-5-5\n")
    write(split, "embeddings.yaml", "provider: local\n")
    write(split, "policies.yaml", "allow_all: true\n")
    write(split, "storage.yaml", "path: .nl2sql\n")

    a, b = load_config(inline), load_config(split / "nl2sql.yaml")

    assert a.model_dump(exclude={"storage"}) == b.model_dump(exclude={"storage"})
    assert a.storage.path == tmp_path / "inline" / ".nl2sql"
    assert b.storage.path == split / ".nl2sql"


def test_datasources_may_be_an_empty_list(tmp_path):
    cfg = load_config(write(tmp_path, "nl2sql.yaml", MINIMAL))
    assert cfg.datasources == []


def test_optional_sections_default_when_absent(tmp_path):
    cfg = load_config(write(tmp_path, "nl2sql.yaml", MINIMAL))
    assert cfg.curated is None
    assert cfg.secrets.providers == []
    assert cfg.settings.pipeline.timeout_s == 60
    assert cfg.api.role_header is None


def test_missing_required_section_is_named(tmp_path):
    text = MINIMAL.replace("embeddings: { provider: local }\n", "")
    error = load_error(write(tmp_path, "nl2sql.yaml", text))
    assert [(p.file, p.path) for p in error.problems] == [("nl2sql.yaml", "embeddings")]
    assert "required" in error.problems[0].message.lower()


def test_version_1_file_points_to_the_mapping(tmp_path):
    path = write(tmp_path, "configs/datasources.yaml", """\
        version: 1
        datasources:
          - id: shop
            connection: { type: sqlite, database: shop.db }
        """)
    error = load_error(path)
    assert len(error.problems) == 1
    message = str(error)
    assert "is a 0.4 config file (`version: 1`)" in message
    assert "0.5 reads `nl2sql.yaml`" in message
    assert "architecture/configuration_v2" in message


def test_missing_version_points_to_the_mapping(tmp_path):
    error = load_error(write(tmp_path, "nl2sql.yaml", MINIMAL.replace("version: 2\n", "")))
    assert len(error.problems) == 1
    assert error.problems[0].path == "version"
    assert "architecture/configuration_v2" in str(error)


def test_glossary_is_not_a_phase_1_section(tmp_path):
    error = load_error(write(tmp_path, "nl2sql.yaml", MINIMAL + "glossary: { file: glossary.yaml }\n"))
    assert [(p.path, p.message) for p in error.problems] == [("glossary", "Unknown key.")]


def test_no_path_reads_nl2sql_config_then_the_working_directory(tmp_path, monkeypatch):
    write(tmp_path / "cwd", "nl2sql.yaml", MINIMAL)
    named = write(tmp_path / "named", "nl2sql.yaml", MINIMAL.replace(".nl2sql", "named-data"))
    monkeypatch.chdir(tmp_path / "cwd")

    monkeypatch.delenv("NL2SQL_CONFIG", raising=False)
    assert load_config().storage.path == tmp_path / "cwd" / ".nl2sql"

    monkeypatch.setenv("NL2SQL_CONFIG", str(named))
    assert load_config().storage.path == tmp_path / "named" / "named-data"


def test_a_missing_file_is_a_config_error(tmp_path):
    error = load_error(tmp_path / "nl2sql.yaml")
    assert "not found" in error.problems[0].message


# ---------------------------------------------------------------- file: includes

def test_a_section_is_inline_or_exactly_a_file_key(tmp_path):
    write(tmp_path, "llm.yaml", "provider: anthropic\nmodel: m\n")
    text = MINIMAL.replace("llm:        { provider: anthropic, model: claude-sonnet-5-5 }",
                           "llm: { file: llm.yaml, model: other }")
    error = load_error(write(tmp_path, "nl2sql.yaml", text))
    assert [(p.file, p.path) for p in error.problems] == [("nl2sql.yaml", "llm")]
    assert "{file: <path>}" in error.problems[0].message


def test_include_paths_are_relative_to_nl2sql_yaml(tmp_path, monkeypatch):
    project = tmp_path / "project"
    write(project, "llm.yaml", "provider: anthropic\nmodel: from-the-file\n")
    path = write(project, "nl2sql.yaml", MINIMAL.replace(
        "llm:        { provider: anthropic, model: claude-sonnet-5-5 }", "llm: { file: llm.yaml }"))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    assert load_config(path).llm.model == "from-the-file"


def test_an_included_file_cannot_include(tmp_path):
    write(tmp_path, "llm.yaml", "file: other.yaml\n")
    write(tmp_path, "other.yaml", "provider: anthropic\nmodel: m\n")
    error = load_error(write(tmp_path, "nl2sql.yaml", MINIMAL.replace(
        "llm:        { provider: anthropic, model: claude-sonnet-5-5 }", "llm: { file: llm.yaml }")))
    assert [p.file for p in error.problems] == ["llm.yaml"]
    assert "cannot include" in error.problems[0].message


def test_an_included_file_has_no_version(tmp_path):
    write(tmp_path, "llm.yaml", "version: 2\nprovider: anthropic\nmodel: m\n")
    error = load_error(write(tmp_path, "nl2sql.yaml", MINIMAL.replace(
        "llm:        { provider: anthropic, model: claude-sonnet-5-5 }", "llm: { file: llm.yaml }")))
    assert [(p.file, p.path) for p in error.problems] == [("llm.yaml", "version")]
    assert "Only nl2sql.yaml" in error.problems[0].message


def test_folder_include_defaults_id_to_file_stem(tmp_path):
    write(tmp_path, "datasources/sales.yaml", "type: postgres\nconnection: { host: db }\n")
    write(tmp_path, "datasources/hr.yaml", "id: hr\ntype: sqlite\n")
    cfg = load_config(write(tmp_path, "nl2sql.yaml",
                            MINIMAL.replace("datasources: []", "datasources: { file: datasources/ }")))
    assert [d.id for d in cfg.datasources] == ["hr", "sales"]


def test_folder_include_reads_yaml_files_only_and_not_subfolders(tmp_path):
    write(tmp_path, "datasources/sales.yaml", "type: sqlite\n")
    write(tmp_path, "datasources/notes.md", "not a datasource\n")
    write(tmp_path, "datasources/archive/old.yaml", "type: sqlite\n")
    cfg = load_config(write(tmp_path, "nl2sql.yaml",
                            MINIMAL.replace("datasources: []", "datasources: { file: datasources/ }")))
    assert [d.id for d in cfg.datasources] == ["sales"]


def test_folder_include_id_must_equal_the_file_name(tmp_path):
    write(tmp_path, "datasources/sales.yaml", "id: marketing\ntype: sqlite\n")
    error = load_error(write(tmp_path, "nl2sql.yaml",
                             MINIMAL.replace("datasources: []", "datasources: { file: datasources/ }")))
    assert [(p.file, p.path) for p in error.problems] == [("datasources/sales.yaml", "id")]
    assert "'sales'" in error.problems[0].message


def test_datasource_file_include_holds_the_list(tmp_path):
    write(tmp_path, "datasources.yaml", "- id: shop\n  type: sqlite\n- id: hr\n  type: sqlite\n")
    cfg = load_config(write(tmp_path, "nl2sql.yaml",
                            MINIMAL.replace("datasources: []", "datasources: { file: datasources.yaml }")))
    assert [d.id for d in cfg.datasources] == ["shop", "hr"]


def test_only_datasources_may_include_a_folder(tmp_path):
    (tmp_path / "llm").mkdir()
    error = load_error(write(tmp_path, "nl2sql.yaml", MINIMAL.replace(
        "llm:        { provider: anthropic, model: claude-sonnet-5-5 }", "llm: { file: llm/ }")))
    assert [(p.file, p.path) for p in error.problems] == [("nl2sql.yaml", "llm.file")]
    assert "folder" in error.problems[0].message


def test_no_interpolation_in_include_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_FILE", "llm.yaml")
    write(tmp_path, "llm.yaml", "provider: anthropic\nmodel: m\n")
    error = load_error(write(tmp_path, "nl2sql.yaml", MINIMAL.replace(
        "llm:        { provider: anthropic, model: claude-sonnet-5-5 }", 'llm: { file: "${env:LLM_FILE}" }')))
    assert [(p.file, p.path) for p in error.problems] == [("nl2sql.yaml", "llm.file")]
    assert "literal" in error.problems[0].message


def test_each_file_is_included_once(tmp_path):
    write(tmp_path, "shared.yaml", "allow_all: true\n")
    text = (MINIMAL.replace("policies:   { allow_all: true }", "policies: { file: shared.yaml }")
                   .replace("embeddings: { provider: local }", "embeddings: { file: shared.yaml }"))
    error = load_error(write(tmp_path, "nl2sql.yaml", text))
    assert any("more than once" in p.message for p in error.problems)


def test_a_missing_included_file_is_named(tmp_path):
    error = load_error(write(tmp_path, "nl2sql.yaml", MINIMAL.replace(
        "llm:        { provider: anthropic, model: claude-sonnet-5-5 }", "llm: { file: llm.yaml }")))
    assert [(p.file, p.path) for p in error.problems] == [("nl2sql.yaml", "llm.file")]
    assert "not found" in error.problems[0].message


# ---------------------------------------------------------------- ${provider:key}

def test_secret_fields_keep_the_reference(tmp_path, monkeypatch):
    monkeypatch.setenv("TEAM_KEY", "sk-plaintext")
    monkeypatch.setenv("SALES_DB_PASSWORD", "hunter2")
    cfg = load_config(write(tmp_path, "nl2sql.yaml", """\
        version: 2
        datasources:
          - id: sales
            type: postgres
            connection: { host: db, password: "${env:SALES_DB_PASSWORD}", token: "${vault:db/sales#token}" }
        llm:
          provider: anthropic
          model: m
          api_key: "${env:TEAM_KEY}"
          agents:
            ast_planner: { api_key: "${env:TEAM_KEY}" }
        embeddings: { provider: openai, api_key: "${env:TEAM_KEY}" }
        policies:   { allow_all: true }
        storage:    { path: .nl2sql }
        """))
    assert cfg.llm.api_key == "${env:TEAM_KEY}"
    assert cfg.llm.agents["ast_planner"].api_key == "${env:TEAM_KEY}"
    assert cfg.embeddings.api_key == "${env:TEAM_KEY}"
    assert cfg.datasources[0].connection["password"] == "${env:SALES_DB_PASSWORD}"
    assert cfg.datasources[0].connection["token"] == "${vault:db/sales#token}"
    dumped = str(cfg.model_dump())
    assert "sk-plaintext" not in dumped and "hunter2" not in dumped


def test_other_fields_resolve_at_load(tmp_path, monkeypatch):
    monkeypatch.setenv("NL2SQL_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TENANT", "acme")
    monkeypatch.setenv("TIMEOUT", "30")
    cfg = load_config(write(tmp_path, "nl2sql.yaml", MINIMAL.replace(".nl2sql", '"${env:NL2SQL_DATA_DIR}"') + """\
settings:
  tenant_id: "${env:TENANT}"
  pipeline: { timeout_s: "${env:TIMEOUT}" }
"""))
    assert cfg.storage.path == tmp_path / "data"
    assert cfg.settings.tenant_id == "acme"
    assert cfg.settings.pipeline.timeout_s == 30


def test_missing_env_in_storage_path_names_the_field(tmp_path, monkeypatch):
    monkeypatch.delenv("NL2SQL_DATA_DIR", raising=False)
    write(tmp_path, "storage.yaml", 'path: "${env:NL2SQL_DATA_DIR}"\n')
    error = load_error(write(tmp_path, "nl2sql.yaml", MINIMAL.replace(
        "storage:    { path: .nl2sql }", "storage: { file: storage.yaml }")))
    assert [(p.file, p.path) for p in error.problems] == [("storage.yaml", "path")]
    assert "NL2SQL_DATA_DIR" in error.problems[0].message
    assert "storage.yaml → path" in str(error)
    assert error.problems[0].fix


def test_reference_inside_a_string_is_an_error(tmp_path, monkeypatch):
    monkeypatch.setenv("U", "me")
    error = load_error(write(tmp_path, "nl2sql.yaml", MINIMAL.replace(
        "datasources: []",
        'datasources: [{id: pg, type: postgres, connection: {url: "postgres://${env:U}@db"}}]')))
    assert [p.path for p in error.problems] == ["datasources[0].connection.url"]
    assert "whole value" in error.problems[0].message


def test_there_is_no_default_syntax(tmp_path):
    error = load_error(write(tmp_path, "nl2sql.yaml", MINIMAL.replace(".nl2sql", '"${env:DATA:-/tmp}"')))
    assert [p.path for p in error.problems] == ["storage.path"]
    assert "no default syntax" in error.problems[0].message


def test_only_env_references_resolve_at_load(tmp_path):
    error = load_error(write(tmp_path, "nl2sql.yaml", MINIMAL.replace(".nl2sql", '"${vault:paths#data}"')))
    assert [p.path for p in error.problems] == ["storage.path"]
    assert "${env:...}" in error.problems[0].message


def test_inside_secrets_only_env_references_are_allowed(tmp_path):
    error = load_error(write(tmp_path, "nl2sql.yaml", MINIMAL + """\
secrets:
  providers:
    - id: vault
      type: hashicorp
      token: "${aws:vault-token}"
"""))
    assert [p.path for p in error.problems] == ["secrets.providers[0].token"]
    assert "${env:...}" in error.problems[0].message


def test_secret_provider_settings_keep_their_references(tmp_path, monkeypatch):
    monkeypatch.setenv("VAULT_TOKEN", "s.plaintext")
    cfg = load_config(write(tmp_path, "nl2sql.yaml", MINIMAL + """\
secrets:
  providers:
    - id: vault
      type: hashicorp
      url: https://vault.internal:8200
      token: "${env:VAULT_TOKEN}"
"""))
    provider = cfg.secrets.providers[0]
    assert provider.model_extra == {"url": "https://vault.internal:8200", "token": "${env:VAULT_TOKEN}"}


# ---------------------------------------------------------------- strict keys

def test_unknown_key_suggests_closest(tmp_path):
    write(tmp_path, "datasources/sales.yaml", "type: postgres\npreferd_tables: [public.revenue]\n")
    error = load_error(write(tmp_path, "nl2sql.yaml",
                             MINIMAL.replace("datasources: []", "datasources: { file: datasources/ }")))
    assert problems_of(error) == [
        ("datasources/sales.yaml", "preferd_tables", "Unknown key. Did you mean 'preferred_tables'?")]


def test_unknown_key_without_a_close_match_has_no_suggestion(tmp_path):
    error = load_error(write(tmp_path, "nl2sql.yaml", MINIMAL.replace(
        "storage:    { path: .nl2sql }", "storage:    { path: .nl2sql, zzz: 1 }")))
    assert problems_of(error) == [("nl2sql.yaml", "storage.zzz", "Unknown key.")]


def test_unknown_agent_name_suggests_the_node_name(tmp_path):
    error = load_error(write(tmp_path, "nl2sql.yaml", MINIMAL.replace(
        "llm:        { provider: anthropic, model: claude-sonnet-5-5 }",
        "llm: { provider: anthropic, model: m, agents: { astplanner: { model: x } } }")))
    [problem] = error.problems
    assert problem.path == "llm.agents.astplanner"
    assert "Did you mean 'ast_planner'?" in problem.message


def test_api_section_rejects_unknown_keys(tmp_path):
    error = load_error(write(tmp_path, "nl2sql.yaml", MINIMAL + "api: { role_headr: X-Role }\n"))
    assert problems_of(error) == [("nl2sql.yaml", "api.role_headr", "Unknown key. Did you mean 'role_header'?")]


# ---------------------------------------------------------------- the error report

def test_every_problem_is_reported_at_once(tmp_path):
    write(tmp_path, "datasources/sales.yaml", "type: postgres\npreferd_tables: []\n")
    write(tmp_path, "llm.yaml", "provider: anthropic\nmodel: m\ntemperature: hot\n")
    error = load_error(write(tmp_path, "nl2sql.yaml", """\
        version: 2
        datasources: { file: datasources/ }
        llm:         { file: llm.yaml }
        embeddings:  { provider: local }
        policies:    { allow_all: true }
        storage:     { path: .nl2sql, extra: 1 }
        """))
    assert [(p.file, p.path) for p in error.problems] == [
        ("datasources/sales.yaml", "preferd_tables"),
        ("llm.yaml", "temperature"),
        ("nl2sql.yaml", "storage.extra"),
    ]
    text = str(error)
    assert text.startswith("nl2sql.yaml has 3 problems:\n\n")
    assert "  datasources/sales.yaml → preferd_tables\n    Unknown key. Did you mean 'preferred_tables'?" in text


def test_one_problem_reads_in_the_singular(tmp_path):
    error = load_error(write(tmp_path, "nl2sql.yaml", MINIMAL + "api: { nope: 1 }\n"))
    assert str(error).startswith("nl2sql.yaml has 1 problem:\n")


def test_a_fix_line_follows_the_message(tmp_path, monkeypatch):
    monkeypatch.delenv("NL2SQL_DATA_DIR", raising=False)
    error = load_error(write(tmp_path, "nl2sql.yaml", MINIMAL.replace(".nl2sql", '"${env:NL2SQL_DATA_DIR}"')))
    assert "\n    Fix: " in str(error)


def test_an_error_never_shows_a_secret_value(tmp_path, monkeypatch):
    monkeypatch.setenv("SALES_DB_PASSWORD", "hunter2")
    error = load_error(write(tmp_path, "nl2sql.yaml", MINIMAL.replace(
        "datasources: []",
        'datasources: [{id: s, connection: {password: "${env:SALES_DB_PASSWORD}"}}]')))
    assert "hunter2" not in str(error)


def test_paths_inside_an_inline_list_carry_the_index(tmp_path):
    error = load_error(write(tmp_path, "nl2sql.yaml", MINIMAL.replace(
        "datasources: []",
        "datasources: [{id: a, type: sqlite}, {id: b, type: sqlite, limits: {row_limit: lots}}]")))
    assert [(p.file, p.path) for p in error.problems] == [("nl2sql.yaml", "datasources[1].limits.row_limit")]


def test_paths_inside_a_datasource_list_file_start_at_the_index(tmp_path):
    write(tmp_path, "datasources.yaml", "- id: a\n  type: sqlite\n  colour: red\n")
    error = load_error(write(tmp_path, "nl2sql.yaml",
                             MINIMAL.replace("datasources: []", "datasources: { file: datasources.yaml }")))
    assert [(p.file, p.path) for p in error.problems] == [("datasources.yaml", "[0].colour")]


def test_an_included_file_that_does_not_parse_is_named(tmp_path):
    write(tmp_path, "llm.yaml", "provider: [anthropic\n")
    error = load_error(write(tmp_path, "nl2sql.yaml", MINIMAL.replace(
        "llm:        { provider: anthropic, model: claude-sonnet-5-5 }", "llm: { file: llm.yaml }")))
    assert [p.file for p in error.problems] == ["llm.yaml"]
    assert "Not valid YAML" in error.problems[0].message


def test_datasource_domains_load_from_yaml(tmp_path):
    write(tmp_path, "datasources/sales.yaml", "type: sqlite\ndomains: [revenue, customers]\n")
    cfg = load_config(write(tmp_path, "nl2sql.yaml",
                            MINIMAL.replace("datasources: []", "datasources: { file: datasources/ }")))
    assert cfg.datasources[0].domains == ["revenue", "customers"]


def test_datasource_domains_reject_non_string_items(tmp_path):
    write(tmp_path, "datasources/sales.yaml", "type: sqlite\ndomains: [revenue, {a: 1}]\n")
    error = load_error(write(tmp_path, "nl2sql.yaml",
                             MINIMAL.replace("datasources: []", "datasources: { file: datasources/ }")))
    assert [(p.file, p.path) for p in error.problems] == [("datasources/sales.yaml", "domains[1]")]


def test_load_config_returns_a_config(tmp_path):
    assert isinstance(load_config(write(tmp_path, "nl2sql.yaml", MINIMAL)), Config)
