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


def test_without_the_anthropic_extra_it_says_what_to_install_without_running(tmp_path, monkeypatch, capsys):
    """It used to die deep in a traceback that asked to report a bug."""
    import sys

    _clear(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_CLAUDE)
    monkeypatch.setitem(sys.modules, "langchain_anthropic", None)  # find_spec reports it missing
    ran = []
    monkeypatch.setattr("nl2sql.cli.commands.demo.demo_command", lambda **kwargs: ran.append(kwargs))
    out = tmp_path / "out.json"

    status = _load().main(["--env-file", str(tmp_path / "missing.env"), "--out", str(out)])

    assert status == 2
    assert not ran
    assert not out.exists()
    assert 'pip install "nl2sql-engine[anthropic]"' in capsys.readouterr().err


def test_a_failed_demo_run_fails_the_script_and_writes_nothing(tmp_path, monkeypatch, capsys):
    """`demo --record` stops when indexing fails; the script must not report success."""
    _clear(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_CLAUDE)

    def _failing(**kwargs):
        raise SystemExit(1)

    monkeypatch.setattr("nl2sql.cli.commands.demo.demo_command", _failing)
    out = tmp_path / "out.json"

    status = _load().main(["--env-file", str(tmp_path / "missing.env"), "--out", str(out)])

    assert status == 1
    assert not out.exists()
    assert "Nothing was recorded" in capsys.readouterr().err


def _recording_demo(answered):
    """A `demo --record` that recorded only ``answered`` and exited 1 for the rest, as it now does."""
    from nl2sql.llm.replay import Recording, ReplayStore

    def _demo(directory, **kwargs):
        directory.mkdir(parents=True, exist_ok=True)
        ReplayStore([Recording("DecomposerResponse", q, {}) for q in answered]).save(directory / "recordings.json")
        raise SystemExit(1)

    return _demo


def test_failed_questions_are_listed_and_fail_the_script(tmp_path, monkeypatch, capsys):
    """A real run said "Recorded 20 of 20" with 10 failed runs."""
    from nl2sql.cli.demo.datasets import DEMO_QUESTIONS
    from nl2sql.llm.replay import ReplayStore

    _clear(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_CLAUDE)
    monkeypatch.setattr("nl2sql.cli.commands.demo.demo_command", _recording_demo(DEMO_QUESTIONS[:1]))
    out = tmp_path / "out.json"

    status = _load().main(["--env-file", str(tmp_path / "missing.env"), "--out", str(out),
                           "--dir", str(tmp_path / "demo")])

    assert status == 1
    assert ReplayStore.load(out).covered(DEMO_QUESTIONS) == DEMO_QUESTIONS[:1]
    printed = capsys.readouterr().out
    assert f"Recorded 1 of {len(DEMO_QUESTIONS)}" in printed
    for question in DEMO_QUESTIONS[1:]:
        assert f"not recorded: {question}" in printed


def test_recordings_left_from_an_earlier_run_are_not_taken_for_this_one(tmp_path, monkeypatch, capsys):
    from nl2sql.cli.demo.datasets import DEMO_QUESTIONS
    from nl2sql.llm.replay import Recording, ReplayStore

    _clear(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_CLAUDE)
    project = tmp_path / "demo"
    project.mkdir()
    stale = project / "recordings.json"
    ReplayStore([Recording("DecomposerResponse", q, {}) for q in DEMO_QUESTIONS]).save(stale)
    import os
    os.utime(stale, (1_000_000_000, 1_000_000_000))

    def _stopped(**kwargs):
        raise SystemExit(1)

    monkeypatch.setattr("nl2sql.cli.commands.demo.demo_command", _stopped)
    out = tmp_path / "out.json"

    status = _load().main(["--env-file", str(tmp_path / "missing.env"), "--out", str(out),
                           "--dir", str(project)])

    assert status == 1
    assert not out.exists()
    assert "Nothing was recorded" in capsys.readouterr().err


def test_output_is_made_safe_before_the_demo_writes_anything(tmp_path, monkeypatch):
    """The script is an entry point of its own: `main()` of the CLI never runs.

    Redirected to a file on Windows, stdout is cp1252 and the demo's first
    check mark killed indexing.
    """
    _clear(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_CLAUDE)
    calls = []
    monkeypatch.setattr("nl2sql.cli.console.configure_output_encoding", lambda: calls.append("configured"))

    def _demo(**kwargs):
        calls.append("demo")
        raise SystemExit(1)

    monkeypatch.setattr("nl2sql.cli.commands.demo.demo_command", _demo)

    _load().main(["--env-file", str(tmp_path / "missing.env"), "--out", str(tmp_path / "out.json")])

    assert calls == ["configured", "demo"]


def test_by_default_it_writes_the_file_the_engine_ships():
    module = _load()

    assert module.OUT.as_posix().endswith("packages/nl2sql/src/nl2sql/cli/demo/recordings/chinook.json")
