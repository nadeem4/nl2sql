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


def test_the_key_is_read_from_the_env_file_without_writing_the_environment(tmp_path, monkeypatch):
    import os

    _clear(monkeypatch)
    env = tmp_path / ".env"
    env.write_text(f"ANTHROPIC_API_KEY={FAKE_CLAUDE}\nOPENAI_API_KEY={FAKE_OPENAI}\n", encoding="utf-8")
    before = dict(os.environ)

    assert _load().recording_key(env) == FAKE_CLAUDE
    assert dict(os.environ) == before


def test_a_key_already_exported_wins_over_the_file(tmp_path, monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_CLAUDE)
    env = tmp_path / ".env"
    env.write_text("ANTHROPIC_API_KEY=something-else-entirely\n", encoding="utf-8")

    assert _load().recording_key(env) == FAKE_CLAUDE


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
    out = tmp_path / "out.json"

    status = _load().main(["--env-file", str(tmp_path / "missing.env"), "--out", str(out)])

    assert status == 2
    assert not out.exists()
    assert 'pip install "nl2sql-engine[anthropic]"' in capsys.readouterr().err


def test_by_default_it_writes_the_file_the_engine_ships():
    module = _load()

    assert module.OUT.as_posix().endswith("packages/nl2sql/src/nl2sql/datasets/recordings/chinook.json")


class _Result:
    def __init__(self, ok):
        self.status = "success" if ok else "error"
        self.final_answer = {"summary": "ok"} if ok else None
        self.errors = [] if ok else [{"message": "boom"}]


def test_record_script_writes_the_store(tmp_path):
    """The recorder keeps what the upstream answered for the questions whose runs succeeded, and the file replays.

    The upstream is the replay provider over a small store: no key, no network.
    """
    from pydantic import BaseModel

    from nl2sql.llm.providers import ResolvedAgent
    from nl2sql.llm.replay import Recorder, Recording, ReplayProvider, ReplayStore
    from nl2sql.llm.wires import structured

    class Thing(BaseModel):
        count: int

    good, bad = "How many customers are there?", "Which genre sold the most?"
    upstream = ReplayProvider(ReplayStore([Recording("Thing", good, {"count": 59}),
                                           Recording("Thing", bad, {"count": 1})]))
    recorder = Recorder(upstream, provider="anthropic")
    client = recorder.create(ResolvedAgent(name="default", model="claude-opus-5", temperature=None))

    class Engine:
        def run_query(self, question, user_context=None):
            structured(client, Thing).invoke(f"[USER_QUERY]\n{question}\n")
            return _Result(question == good)

    recorded = _load().record_answers(Engine(), recorder, [good, bad])
    out = tmp_path / "out.json"
    recorder.store.save(out)

    assert recorded == [good]
    saved = ReplayStore.load(out)
    assert [(r.name, r.when) for r in saved._recordings] == [("Thing", good)]
    assert (saved.provider, saved.model) == ("anthropic", "claude-opus-5")


class _Turn:
    def __init__(self, status, steps=()):
        self.status, self.steps = status, list(steps)


def test_record_script_keeps_only_the_conversation_messages_that_were_answered(tmp_path):
    """A turn that ends answered or asking which reading keeps its model steps; a failed one loses them."""
    from nl2sql.llm.replay import Recorder, Recording, ReplayProvider, ReplayStore

    module = _load()
    good, bad, clarify = "Who spends most?", "And the rest?", "Which customers are best?"
    store = ReplayStore([Recording("ModelStep", f'"{m}" (turn 1, step 1)', {"reply": "x"}) for m in (good, bad, clarify)])
    recorder = Recorder(ReplayProvider(store), provider="anthropic")
    recorder.store.add(Recording("ModelStep", f'"{good}" (turn 1, step 1)', {"reply": "x"}))
    recorder.store.add(Recording("ModelStep", f'"{bad}" (turn 1, step 1)', {"reply": "x"}))
    recorder.store.add(Recording("ModelStep", f'"{clarify}" (turn 1, step 1)', {"reply": "x"}))

    statuses = {good: "answered", bad: "error", clarify: "needs_clarification"}

    class Conversations:
        def start(self, user_context=None):
            return type("C", (), {"id": "c1"})()

        def ask(self, conversation_id, text, user_context=None):
            return _Turn(statuses[text])

    class Engine:
        conversations = Conversations()

    kept = module.record_conversations(Engine(), recorder, [[good], [bad], [clarify]])

    assert kept == [good, clarify]
    assert [r.when for r in recorder.store._recordings] == [f'"{good}" (turn 1, step 1)',
                                                           f'"{clarify}" (turn 1, step 1)']


def test_the_chat_scripts_are_the_guided_questions_the_follow_ups_and_one_clarification():
    from nl2sql.datasets.demo import DEMO_QUESTIONS

    module = _load()
    scripts = module.chat_scripts(list(DEMO_QUESTIONS))

    assert scripts[: len(DEMO_QUESTIONS)] == [[q] for q in DEMO_QUESTIONS]
    assert [s for s in scripts if len(s) == 2] == [list(p) for p in module.FOLLOW_UPS]
    assert scripts[-1] == [module.CLARIFICATION_QUESTION]
