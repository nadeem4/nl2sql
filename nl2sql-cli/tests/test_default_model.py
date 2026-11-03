"""Every config the CLI writes defaults OpenAI to gpt-5.4.

gpt-4o's 30,000 tokens-per-minute limit on the owner's account was below a
single question with one retry (about 33.5k tokens). gpt-5.4 has 500,000 and
accepts ``temperature=0``, so ``nl2sql init`` and the demo can keep sending it.
"""
import yaml

from nl2sql.config import init_project
from nl2sql.llm.providers import DEFAULT_OPENAI_MODEL, default_model_for


def test_openai_default_model_is_gpt_5_4():
    assert DEFAULT_OPENAI_MODEL == "gpt-5.4"
    assert default_model_for("openai") == "gpt-5.4"
    # OpenRouter's default is unchanged: its gpt-5.4 id was not verified.
    assert default_model_for("openrouter") == "anthropic/claude-sonnet-4.5"


def test_the_demo_writes_gpt_5_4_with_temperature_zero(tmp_path):
    init_project(tmp_path, demo=True)
    llm = yaml.safe_load((tmp_path / "llm.yaml").read_text(encoding="utf-8"))
    assert llm["model"] == "gpt-5.4"
    assert llm["temperature"] == 0.0
