"""Every config the CLI writes defaults OpenAI to gpt-5.4.

gpt-4o's 30,000 tokens-per-minute limit on the owner's account was below a
single question with one retry (about 33.5k tokens). gpt-5.4 has 500,000 and
accepts ``temperature=0``, so the demo and setup can keep sending it.
"""
from pathlib import Path

import yaml
from rich.console import Console

from nl2sql.cli.commands import setup as cli_setup
from nl2sql.llm.providers import DEFAULT_OPENAI_MODEL, default_model_for
from nl2sql.cli.demo import DemoManager
from nl2sql.cli.generators.llm import LLMGenerator
from nl2sql.configs import AgentConfig, ConfigManager, LLMFileConfig

# Built at run time so no key-shaped literal sits in the source.
OPENAI_KEY = "-".join(["sk", "test", "not", "a", "real", "key"])
OPENROUTER_KEY = "-".join(["sk", "or", "v1", "test", "not", "a", "real", "key"])


def test_openai_default_model_is_gpt_5_4():
    assert DEFAULT_OPENAI_MODEL == "gpt-5.4"
    assert default_model_for("openai") == "gpt-5.4"
    # OpenRouter's default is unchanged: its gpt-5.4 id was not verified.
    assert default_model_for("openrouter") == "anthropic/claude-sonnet-4.5"


def test_the_demo_writes_gpt_5_4_with_temperature_zero(tmp_path):
    DemoManager(Console(), tmp_path).setup_demo()
    llm = yaml.safe_load((tmp_path / "configs" / "llm.demo.yaml").read_text(encoding="utf-8"))
    assert llm["default"]["model"] == "gpt-5.4"
    assert llm["default"]["temperature"] == 0.0


def test_the_checked_in_demo_config_names_gpt_5_4():
    repo_root = Path(__file__).resolve().parents[4]
    llm = yaml.safe_load((repo_root / "configs" / "llm.demo.yaml").read_text(encoding="utf-8"))
    assert llm["default"]["model"] == "gpt-5.4"


def _prepare(tmp_path, monkeypatch):
    monkeypatch.setattr(cli_setup, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(cli_setup, "LLM_CONFIG", tmp_path / "configs" / "llm.yaml")
    (tmp_path / "configs").mkdir(parents=True, exist_ok=True)


def test_setup_with_an_openai_key_writes_gpt_5_4(tmp_path, monkeypatch):
    _prepare(tmp_path, monkeypatch)
    cli_setup._configure_llm(None, api_key=OPENAI_KEY)
    llm = yaml.safe_load((tmp_path / "configs" / "llm.yaml").read_text(encoding="utf-8"))
    assert llm["default"]["model"] == "gpt-5.4"


def test_setup_with_an_openrouter_key_keeps_its_default(tmp_path, monkeypatch):
    _prepare(tmp_path, monkeypatch)
    cli_setup._configure_llm(None, api_key=OPENROUTER_KEY)
    llm = yaml.safe_load((tmp_path / "configs" / "llm.yaml").read_text(encoding="utf-8"))
    assert llm["default"]["model"] == "anthropic/claude-sonnet-4.5"


def test_a_null_temperature_survives_generate_then_load(tmp_path):
    """The generator drops ``None`` fields; a dropped temperature reloads as 0.0."""
    config = LLMFileConfig(
        default=AgentConfig(provider="openai", model="gpt-5.4"),
        agents={"refiner": AgentConfig(provider="openai", model="gpt-5.5", temperature=None)},
    )
    path = tmp_path / "llm.yaml"
    path.write_text(LLMGenerator.generate(config), encoding="utf-8")

    loaded = ConfigManager(tmp_path).load_llm(path)
    assert loaded.default.temperature == 0.0
    assert loaded.agents["refiner"].temperature is None
