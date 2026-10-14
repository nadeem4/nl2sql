"""``init_project`` writes a config that ``load_config`` reads, and ``demo_config`` is the demo.

docs/architecture/configuration_v2.md, section 10 (CLI) and the PR 6 row.
"""
import sqlite3
from pathlib import Path

import pytest
import yaml

from nl2sql.config import LLM, Config, Policies, demo_config, init_project, load_config
from nl2sql.datasets import CHINOOK_DB_PATH
from nl2sql.datasets.demo import DEMO_QUESTIONS_BY_DATASOURCE
from nl2sql.llm.providers import DEFAULT_ANTHROPIC_MODEL, DEFAULT_OPENAI_MODEL

FAKE_KEYS = {
    "OPENAI_API_KEY": "sk-proj-init-test-openai-0000000000",
    "ANTHROPIC_API_KEY": "sk-ant-init-test-anthropic-000000000",
    "OPENROUTER_API_KEY": "sk-or-init-test-openrouter-00000000",
}


def _texts(folder: Path) -> dict:
    return {p: p.read_text(encoding="utf-8") for p in folder.rglob("*") if p.is_file() and p.suffix != ".sqlite"}


def _without_paths(config: Config) -> Config:
    """``config`` with ``storage.path`` and each SQLite file reduced to its name."""
    datasources = [
        d.model_copy(update={"connection": {**d.connection, "database": Path(d.connection["database"]).name}})
        for d in config.datasources
    ]
    return config.model_copy(update={"datasources": datasources,
                                     "storage": config.storage.model_copy(update={"path": Path("storage")})})


# ---------------------------------------------------------------- init

def test_init_output_loads_cleanly(tmp_path):
    init_project(tmp_path)

    config = load_config(tmp_path / "nl2sql.yaml")

    assert [d.id for d in config.datasources] == ["example"]
    assert config.datasources[0].type == "sqlite"
    assert (config.llm.provider, config.llm.model) == ("openai", DEFAULT_OPENAI_MODEL)
    assert config.embeddings.provider == "local"
    assert config.policies == Policies.allow_all()
    assert config.storage.path == tmp_path / ".nl2sql"
    assert config.curated is None


def test_init_writes_each_section_in_its_own_file(tmp_path):
    written = init_project(tmp_path)

    toc = yaml.safe_load((tmp_path / "nl2sql.yaml").read_text(encoding="utf-8"))
    assert toc == {
        "version": 2,
        "datasources": {"file": "datasources/"},
        "llm": {"file": "llm.yaml"},
        "embeddings": {"file": "embeddings.yaml"},
        "policies": {"file": "policies.yaml"},
        "storage": {"file": "storage.yaml"},
    }
    names = {p.relative_to(tmp_path).as_posix() for p in written}
    assert names == {"nl2sql.yaml", "datasources/example.yaml", "llm.yaml", "embeddings.yaml",
                     "policies.yaml", "storage.yaml", ".gitignore"}


def test_init_ignores_the_data_folder_and_env_in_git(tmp_path):
    init_project(tmp_path)

    lines = (tmp_path / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".nl2sql/" in lines
    assert ".env" in lines


def test_init_keeps_an_existing_gitignore_and_adds_what_is_missing(tmp_path):
    (tmp_path / ".gitignore").write_text("node_modules/\n.env\n", encoding="utf-8")

    init_project(tmp_path)

    lines = (tmp_path / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert lines[:2] == ["node_modules/", ".env"]
    assert lines.count(".env") == 1
    assert ".nl2sql/" in lines


@pytest.mark.parametrize("provider, model, temperature", [
    ("openai", DEFAULT_OPENAI_MODEL, 0.0),
    ("anthropic", DEFAULT_ANTHROPIC_MODEL, None),
])
def test_provider_picks_the_llm_and_its_default_model(tmp_path, provider, model, temperature):
    init_project(tmp_path, provider=provider)

    llm = load_config(tmp_path / "nl2sql.yaml").llm
    assert (llm.provider, llm.model, llm.temperature) == (provider, model, temperature)


def test_an_unknown_provider_is_refused_before_anything_is_written(tmp_path):
    with pytest.raises(ValueError, match="openai"):
        init_project(tmp_path, provider="antropic")

    assert list(tmp_path.iterdir()) == []


# ---------------------------------------------------------------- --force

def test_force_is_needed_to_overwrite(tmp_path):
    init_project(tmp_path)
    (tmp_path / "llm.yaml").write_text("provider: openai\nmodel: mine\n", encoding="utf-8")

    with pytest.raises(FileExistsError) as raised:
        init_project(tmp_path, provider="anthropic")

    assert "llm.yaml" in str(raised.value)
    assert (tmp_path / "llm.yaml").read_text(encoding="utf-8") == "provider: openai\nmodel: mine\n"


def test_nothing_is_written_when_one_file_already_exists(tmp_path):
    (tmp_path / "storage.yaml").write_text("path: elsewhere\n", encoding="utf-8")

    with pytest.raises(FileExistsError):
        init_project(tmp_path)

    assert [p.name for p in tmp_path.iterdir()] == ["storage.yaml"]


def test_force_overwrites(tmp_path):
    init_project(tmp_path)

    init_project(tmp_path, provider="anthropic", force=True)

    assert load_config(tmp_path / "nl2sql.yaml").llm.provider == "anthropic"


# ---------------------------------------------------------------- keys

@pytest.mark.parametrize("demo", [False, True])
@pytest.mark.parametrize("provider", ["openai", "anthropic", "openrouter"])
def test_no_key_is_ever_written_into_yaml(tmp_path, monkeypatch, demo, provider):
    for name, value in FAKE_KEYS.items():
        monkeypatch.setenv(name, value)

    init_project(tmp_path, demo=demo, provider=provider)

    for path, text in _texts(tmp_path).items():
        for value in FAKE_KEYS.values():
            assert value not in text, path
    config = load_config(tmp_path / "nl2sql.yaml")
    assert config.llm.api_key is None
    assert config.embeddings.api_key is None


# ---------------------------------------------------------------- --demo

def test_demo_output_equals_the_demo_config_apart_from_paths(tmp_path):
    init_project(tmp_path, demo=True)

    written = load_config(tmp_path / "nl2sql.yaml")

    assert _without_paths(written) == _without_paths(demo_config())


def test_demo_output_follows_the_provider(tmp_path):
    init_project(tmp_path, demo=True, provider="anthropic")

    written = load_config(tmp_path / "nl2sql.yaml")
    expected = demo_config(llm=LLM(provider="anthropic", model=DEFAULT_ANTHROPIC_MODEL, temperature=None))
    assert _without_paths(written) == _without_paths(expected)


def test_demo_copies_the_three_databases_next_to_the_config(tmp_path):
    init_project(tmp_path, demo=True)

    config = load_config(tmp_path / "nl2sql.yaml")
    for datasource in config.datasources:
        database = tmp_path / datasource.connection["database"]
        assert database.parent == tmp_path / "data"
        con = sqlite3.connect(database)
        try:
            assert con.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0] > 0
        finally:
            con.close()


def test_demo_writes_one_file_per_datasource_and_the_curated_section(tmp_path):
    init_project(tmp_path, demo=True)

    assert sorted(p.name for p in (tmp_path / "datasources").iterdir()) == [
        "chinook.yaml", "support.yaml", "webanalytics.yaml"]
    assert not (tmp_path / "datasources" / "example.yaml").exists()
    toc = yaml.safe_load((tmp_path / "nl2sql.yaml").read_text(encoding="utf-8"))
    assert toc["curated"] == {"file": "curated.yaml"}


def test_demo_curated_sql_reads_as_written(tmp_path):
    init_project(tmp_path, demo=True)

    text = (tmp_path / "curated.yaml").read_text(encoding="utf-8")
    assert "sql: |" in text


# ---------------------------------------------------------------- demo_config

def test_demo_config_carries_sample_questions_inside_each_datasource():
    config = demo_config()

    assert {d.id: d.sample_questions for d in config.datasources} == DEMO_QUESTIONS_BY_DATASOURCE
    assert all(d.description for d in config.datasources)


def test_demo_config_opens_the_packaged_databases_read_only():
    config = demo_config()

    chinook = next(d for d in config.datasources if d.id == "chinook")
    assert Path(chinook.connection["database"]) == CHINOOK_DB_PATH
    assert all(d.connection["options"]["read_only"] is True for d in config.datasources)
    assert all(d.limits.row_limit == 10_000 for d in config.datasources)


def test_demo_config_has_the_demo_roles_and_curated_answers():
    config = demo_config()

    assert set(config.policies.roles) == {"admin", "analyst", "viewer"}
    assert config.policies.allow_all is False
    assert set(config.curated.datasources) == {"chinook"}
    assert config.storage.path == Path(".nl2sql-demo")


def test_demo_config_keeps_the_demos_own_settings():
    """What the 0.4 demo's .env.demo set, now in the demo's config.

    A live run walks six model calls (one OpenAI run took 136 s), so the demo
    allows 300 s; it traces every run for the playground's Debug drill-down;
    and a refusal names the table, since showing one is the demo's point.
    """
    config = demo_config()

    assert config.settings.pipeline.timeout_s == 300
    assert config.settings.traces.mode == "always"
    assert config.policies.refusal_names_tables is True


def test_demo_output_writes_the_settings_section(tmp_path):
    init_project(tmp_path, demo=True)

    toc = yaml.safe_load((tmp_path / "nl2sql.yaml").read_text(encoding="utf-8"))
    assert toc["settings"] == {"file": "settings.yaml"}
    assert load_config(tmp_path / "nl2sql.yaml").settings.traces.mode == "always"


def test_demo_config_takes_its_llm_and_storage_path(tmp_path):
    llm = LLM(provider="openai", model="gpt-5.4-mini", base_url="http://127.0.0.1:9/v1")

    config = demo_config(llm=llm, path=tmp_path / "store")

    assert config.llm == llm
    assert config.storage.path == tmp_path / "store"
