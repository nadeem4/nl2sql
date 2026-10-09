"""The decisions in ``scripts/record_demo_answers.py`` that need no model.

The recording itself, through a stand-in Anthropic upstream, is the e2e test
``tests/e2e/test_record_demo_answers_fake_llm.py``. Nothing here uses a real key.
"""
import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[4] / "scripts" / "record_demo_answers.py"
FAKE_CLAUDE = "-".join(["sk", "ant", "api03", "recordscript" + "e" * 24 + "7f1a"])
FAKE_OPENAI = "-".join(["sk", "proj", "recordscript" + "f" * 24 + "2b9c"])


def _load():
    spec = importlib.util.spec_from_file_location("record_demo_answers", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _clear(monkeypatch):
    for name in ("OPENAI_API_KEY", "OPENROUTER_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def test_the_key_is_read_from_the_env_file_like_the_engine_reads_its_own(tmp_path, monkeypatch):
    _clear(monkeypatch)
    env = tmp_path / ".env"
    env.write_text(f"ANTHROPIC_API_KEY={FAKE_CLAUDE}\nOPENAI_API_KEY={FAKE_OPENAI}\n", encoding="utf-8")

    import os

    assert _load().recording_key_present(env) is True
    assert os.environ["ANTHROPIC_API_KEY"] == FAKE_CLAUDE
    # Another provider's key is set aside, so --record cannot pick it first.
    assert "OPENAI_API_KEY" not in os.environ


def test_a_key_already_exported_wins_over_the_file(tmp_path, monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_CLAUDE)
    env = tmp_path / ".env"
    env.write_text("ANTHROPIC_API_KEY=something-else-entirely\n", encoding="utf-8")

    import os

    assert _load().recording_key_present(env) is True
    assert os.environ["ANTHROPIC_API_KEY"] == FAKE_CLAUDE


def test_no_key_records_nothing_and_says_so_without_running(tmp_path, monkeypatch, capsys):
    _clear(monkeypatch)
    out = tmp_path / "out.json"

    status = _load().main(["--env-file", str(tmp_path / "missing.env"), "--out", str(out)])

    assert status == 2
    assert not out.exists()
    assert "No ANTHROPIC_API_KEY" in capsys.readouterr().err


def test_by_default_it_writes_the_file_the_engine_ships():
    module = _load()

    assert module.OUT.as_posix().endswith("packages/nl2sql/src/nl2sql/cli/demo/recordings/chinook.json")
