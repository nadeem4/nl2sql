"""The demo's example questions reach the index.

The env template wrote ``ROUTING_EXAMPLES=`` while settings read
``SAMPLE_QUESTIONS``, and the default ``configs/sample_questions.yaml`` does
not exist in a demo folder, so ``get_example_questions`` returned ``[]`` and
the datasource entry said "Examples: N/A".
"""
from __future__ import annotations

from dotenv import load_dotenv
from rich.console import Console

from nl2sql.cli.demo.chinook import CHINOOK_QUESTIONS
from nl2sql.cli.demo.manager import DemoManager
from nl2sql.cli.generators.env import EnvFileGenerator
from nl2sql.common.settings import reload_settings
from nl2sql.configs import ConfigManager


def _questions_after_loading(project, monkeypatch):
    monkeypatch.chdir(project)
    load_dotenv(project / ".env.demo", override=True)
    reload_settings()
    return ConfigManager().get_example_questions("chinook")


def test_the_env_template_names_the_setting_the_engine_reads():
    content = EnvFileGenerator.generate("demo")

    assert "SAMPLE_QUESTIONS=configs/sample_questions.demo.yaml" in content
    assert "ROUTING_EXAMPLES" not in content


def test_a_new_demo_folder_indexes_its_example_questions(tmp_path, monkeypatch):
    DemoManager(Console(quiet=True), tmp_path).setup_demo()

    assert _questions_after_loading(tmp_path, monkeypatch) == list(CHINOOK_QUESTIONS)


def test_an_existing_folder_with_the_old_name_still_gets_its_questions(tmp_path, monkeypatch):
    """Folders written before the fix say ROUTING_EXAMPLES; they keep working."""
    DemoManager(Console(quiet=True), tmp_path).setup_demo()
    env = tmp_path / ".env.demo"
    env.write_text(env.read_text(encoding="utf-8").replace("SAMPLE_QUESTIONS=", "ROUTING_EXAMPLES="),
                   encoding="utf-8")
    monkeypatch.delenv("SAMPLE_QUESTIONS", raising=False)

    assert _questions_after_loading(tmp_path, monkeypatch) == list(CHINOOK_QUESTIONS)
