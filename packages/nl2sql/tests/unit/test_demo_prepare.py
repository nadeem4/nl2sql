"""``prepare_demo``, ``engine.add_demo()`` and the ``Recorder`` (docs/architecture/wave_d.md, row G2b).

The indexing step is replaced by a recorder here: building the real index needs the local
embedding model, which the e2e tests (``tests/e2e/test_replay_fake_llm.py``) do use.
"""
from __future__ import annotations

import pytest
import yaml
from pydantic import BaseModel

import nl2sql
from nl2sql.config import default_llm, starter_config
from nl2sql.datasets.demo import DEMO_QUESTIONS, DEMO_QUESTIONS_BY_DATASOURCE
from nl2sql.llm.providers import ResolvedAgent
from nl2sql.llm.replay import Recorder, Recording, ReplayProvider, ReplayStore, packaged_store
from nl2sql.llm.wires import structured
from nl2sql.policies import UserContext

FAKE_OPENAI = "-".join(["sk", "proj", "preparedemo" + "p" * 25 + "4d2e"])


def _no_keys(monkeypatch):
    for name in ("OPENAI_API_KEY", "OPENROUTER_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def _llm_file(folder):
    return yaml.safe_load((folder / "llm.yaml").read_text(encoding="utf-8"))


def test_prepare_demo_writes_once_and_indexes_when_missing(tmp_path, monkeypatch):
    _no_keys(monkeypatch)
    indexed = []
    monkeypatch.setattr("nl2sql.config.project._index_if_missing", indexed.append)
    folder = tmp_path / "demo"

    first = nl2sql.prepare_demo(folder)
    written = first.read_bytes()
    (folder / "marker.txt").write_text("kept", encoding="utf-8")
    second = nl2sql.prepare_demo(folder)

    assert first == second == folder / "nl2sql.yaml"
    assert second.read_bytes() == written and (folder / "marker.txt").read_text(encoding="utf-8") == "kept"
    assert indexed == [first, first]  # every call checks the index; the project is written the once
    assert (folder / "data").is_dir() and (folder / "glossary.yaml").is_file()


def test_prepare_demo_picks_replay_without_a_key(tmp_path, monkeypatch):
    _no_keys(monkeypatch)
    monkeypatch.setattr("nl2sql.config.project._index_if_missing", lambda path: None)

    keyless = nl2sql.prepare_demo(tmp_path / "keyless").parent
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_OPENAI)
    keyed = nl2sql.prepare_demo(tmp_path / "keyed").parent

    assert _llm_file(keyless)["provider"] == "replay"
    assert _llm_file(keyed)["provider"] == "openai"
    assert FAKE_OPENAI not in (keyed / "llm.yaml").read_text(encoding="utf-8")


def test_init_project_is_exported():
    assert nl2sql.init_project.__name__ == "init_project"


def _engine(tmp_path, **changes):
    """An engine with no datasource and no knowledge; ``replay`` is added by hand, as an install's entry point does."""
    from nl2sql.llm.replay import REPLAY
    from nl2sql.registry import Registry

    registry = Registry.default()
    registry.add_llm("replay", REPLAY)
    config = starter_config(llm=default_llm("replay"), path=tmp_path / "store").model_copy(
        update={"knowledge": [], **changes})
    return nl2sql.NL2SQL(config, registry=registry)


def test_add_demo_adds_the_samples_and_indexes_them(tmp_path, monkeypatch):
    built = []

    def _build(self, ids=None, **kwargs):
        built.append((list(ids), kwargs["on_datasource"]))
        return "built"

    monkeypatch.setattr("nl2sql.api.indexing_api.IndexAPI.build", _build)
    seen = []
    with _engine(tmp_path) as engine:
        result = engine.add_demo(on_datasource=seen.append)
        listed = {d.id: d for d in engine.datasources.list()}
        again = engine.add_demo()

    assert result == again == "built"
    assert set(listed) == set(DEMO_QUESTIONS_BY_DATASOURCE)
    assert built[0] == (sorted(DEMO_QUESTIONS_BY_DATASOURCE, key=list(DEMO_QUESTIONS_BY_DATASOURCE).index), seen.append) \
        or built[0][1] == seen.append
    for datasource_id, questions in DEMO_QUESTIONS_BY_DATASOURCE.items():
        assert listed[datasource_id].sample_questions[: len(questions)] == questions
    assert len(engine._runtime.config.datasources) == 3 and len(engine._runtime.config.knowledge) == 1  # the glossary, added once


def test_add_demo_needs_an_admin_when_a_caller_is_named(tmp_path, monkeypatch):
    monkeypatch.setattr("nl2sql.api.indexing_api.IndexAPI.build", lambda self, ids=None, **kw: None)
    policies = nl2sql.config.Policies(roles={"viewer": nl2sql.config.Role(description="read only")},
                                      admin_roles=["admin"])
    with _engine(tmp_path, policies=policies) as engine:
        with pytest.raises(PermissionError):
            engine.add_demo(user_context=UserContext(user_id="v", roles=["viewer"]))
        assert engine.datasources.list() == []


class Thing(BaseModel):
    count: int


def test_the_recorder_keeps_what_the_upstream_answered_and_replay_gives_it_back(tmp_path):
    question = "How many customers are there?"
    upstream = ReplayProvider(ReplayStore([Recording("Thing", question, {"count": 59})]))
    recorder = Recorder(upstream, provider="anthropic")
    client = recorder.create(ResolvedAgent(name="default", model="claude-opus-5", temperature=None))

    answer = structured(client, Thing).invoke(f"Plan.\n\n[USER_QUERY]\n{question}\n")
    path = tmp_path / "out.json"
    recorder.store.save(path)
    replayed = structured(ReplayProvider(ReplayStore.load(path)).create(
        ResolvedAgent(name="default", model="x", temperature=None)), Thing).invoke(f"[USER_QUERY]\n{question}\n")

    assert answer == replayed == Thing(count=59)
    assert (recorder.store.provider, recorder.store.model) == ("anthropic", "claude-opus-5")
    assert recorder.api_key_env is None and recorder.interface_version == 2


def test_replay_covered():
    """Every guided question of the demo has a recorded decomposer answer in the packaged store."""
    assert packaged_store().covered(DEMO_QUESTIONS) == DEMO_QUESTIONS
