"""`setup --api-key` stores the key where its provider will look for it.

An OpenRouter key written to `OPENAI_API_KEY` with `provider: openai` is a key
the engine can never use: the registry reads `OPENROUTER_API_KEY` for that
provider and the base URL points at the wrong host. The provider is inferred
from the key shape, the same rule `demo --api-key` uses.
"""
import yaml

from nl2sql.cli.commands import setup as cli_setup

OPENROUTER_KEY = "sk-or-v1-test-not-a-real-key"
OPENAI_KEY = "sk-test-not-a-real-key"


def _prepare(tmp_path, monkeypatch):
    monkeypatch.setattr(cli_setup, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(cli_setup, "LLM_CONFIG", tmp_path / "configs" / "llm.yaml")
    (tmp_path / "configs").mkdir(parents=True, exist_ok=True)


def test_openrouter_key_configures_openrouter(tmp_path, monkeypatch):
    _prepare(tmp_path, monkeypatch)

    cli_setup._configure_llm(None, api_key=OPENROUTER_KEY)
    cli_setup._configure_env_file("dev", api_key=OPENROUTER_KEY)

    llm = yaml.safe_load((tmp_path / "configs" / "llm.yaml").read_text(encoding="utf-8"))
    assert llm["default"]["provider"] == "openrouter"
    assert llm["default"]["api_key"] == "${env:OPENROUTER_API_KEY}"
    env_dev = (tmp_path / ".env.dev").read_text(encoding="utf-8")
    assert f"OPENROUTER_API_KEY={OPENROUTER_KEY}" in env_dev


def test_openai_key_still_configures_openai(tmp_path, monkeypatch):
    _prepare(tmp_path, monkeypatch)

    cli_setup._configure_llm(None, api_key=OPENAI_KEY)
    cli_setup._configure_env_file("dev", api_key=OPENAI_KEY)

    llm = yaml.safe_load((tmp_path / "configs" / "llm.yaml").read_text(encoding="utf-8"))
    assert llm["default"]["provider"] == "openai"
    assert llm["default"]["api_key"] == "${env:OPENAI_API_KEY}"
    env_dev = (tmp_path / ".env.dev").read_text(encoding="utf-8")
    assert f"OPENAI_API_KEY={OPENAI_KEY}" in env_dev


def test_anthropic_key_configures_claude(tmp_path, monkeypatch):
    # Built at run time so no secret scanner mistakes a fixture for a leaked key.
    anthropic_key = "-".join(["sk", "ant", "api03", "test", "not", "a", "real", "key"])
    _prepare(tmp_path, monkeypatch)

    cli_setup._configure_llm(None, api_key=anthropic_key)
    cli_setup._configure_env_file("dev", api_key=anthropic_key)

    llm = yaml.safe_load((tmp_path / "configs" / "llm.yaml").read_text(encoding="utf-8"))
    assert llm["default"]["provider"] == "anthropic"
    assert llm["default"]["model"] == "claude-opus-5"
    # Claude Opus 5 rejects a temperature, so none is written.
    assert llm["default"]["temperature"] is None
    assert llm["default"]["api_key"] == "${env:ANTHROPIC_API_KEY}"
    env_dev = (tmp_path / ".env.dev").read_text(encoding="utf-8")
    assert f"ANTHROPIC_API_KEY={anthropic_key}" in env_dev


def test_the_key_is_not_echoed_by_setup(tmp_path, monkeypatch, capsys):
    _prepare(tmp_path, monkeypatch)

    cli_setup._configure_llm(None, api_key=OPENROUTER_KEY)
    cli_setup._configure_env_file("dev", api_key=OPENROUTER_KEY)

    assert OPENROUTER_KEY not in capsys.readouterr().out
