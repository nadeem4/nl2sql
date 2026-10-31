"""``prepare_demo``, ``engine.add_demo()`` and the ``Recorder`` (docs/architecture/wave_d.md, row G2b).

The indexing step is replaced by a recorder here: building the real index needs the local
embedding model, which the e2e tests (``tests/e2e/test_replay_fake_llm.py``) do use.
"""
from __future__ import annotations

from pathlib import Path

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
    assert built[0][0] == list(DEMO_QUESTIONS_BY_DATASOURCE) and built[0][1] == seen.append
    for datasource_id, questions in DEMO_QUESTIONS_BY_DATASOURCE.items():
        assert listed[datasource_id].sample_questions[: len(questions)] == questions
    assert len(engine._runtime.config.datasources) == 3 and len(engine._runtime.config.knowledge) == 1  # the glossary, added once


def test_add_demo_needs_an_admin_when_a_caller_is_named(tmp_path, monkeypatch):
    monkeypatch.setattr("nl2sql.api.indexing_api.IndexAPI.build", lambda self, ids=None, **kw: None)
    policies = nl2sql.config.Policies(roles={"viewer": nl2sql.config.Role(description="read only")},
                                      admin_roles=["admin"])
    with _engine(tmp_path, policies=policies) as engine:
        with pytest.raises(nl2sql.common.errors.NotAuthorized, match="Adding the demo needs one of the admin roles"):
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


class _Health:
    """Stands in for the engine ``_index_if_missing`` opens."""

    def __init__(self, status, built):
        self.status, self.built = status, built
        self.index = self

    def health(self):
        return {"status": self.status}

    def build(self):
        self.built.append(True)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.mark.parametrize("status, builds", [("ok", 0), ("missing", 1)])
def test_the_index_is_built_only_when_it_is_not_ok(tmp_path, monkeypatch, status, builds):
    from nl2sql.config import project

    built = []
    monkeypatch.setattr(project, "load_config", lambda path: None)
    monkeypatch.setattr("nl2sql.public_api.NL2SQL", lambda config: _Health(status, built))

    project._index_if_missing(tmp_path / "nl2sql.yaml")

    assert len(built) == builds


def test_prepare_demo_picks_ollama_when_reachable(tmp_path, monkeypatch):
    _no_keys(monkeypatch)
    monkeypatch.setattr("nl2sql.config.project._index_if_missing", lambda path: None)
    monkeypatch.setattr("nl2sql.llm.providers.ollama_models", lambda: ["llama3.2:latest"])

    folder = nl2sql.prepare_demo(tmp_path / "local").parent

    assert _llm_file(folder)["provider"] == "ollama" and _llm_file(folder)["model"] == "llama3.2:latest"
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_OPENAI)  # a key still wins over a local daemon
    assert _llm_file(nl2sql.prepare_demo(tmp_path / "keyed").parent)["provider"] == "openai"


def test_the_replay_entry_point_is_declared_and_builds_a_replay_model():
    import importlib
    import tomllib
    from pathlib import Path

    pyproject = Path(nl2sql.__file__).resolve().parents[2] / "pyproject.toml"
    declared = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["entry-points"]["nl2sql.llms"]
    module, _, attribute = declared["replay"].partition(":")
    provider = getattr(importlib.import_module(module), attribute)

    from nl2sql.llm.replay import ReplayChatModel

    client = provider.create(ResolvedAgent(name="default", model="recorded", temperature=None))
    assert isinstance(client, ReplayChatModel)


# ---------------------------------------------------------------- add_demo, scoped and persisted

def _no_build(monkeypatch):
    monkeypatch.setattr("nl2sql.api.indexing_api.IndexAPI.build", lambda self, ids=None, **kw: "built")


def test_add_demo_adds_the_verified_queries_and_a_glossary_copy_inside_the_project(tmp_path, monkeypatch):
    from nl2sql.datasets import DEMO_GLOSSARY_PATH

    _no_build(monkeypatch)
    with _engine(tmp_path / "project") as engine:
        engine.add_demo()
        again = engine._runtime.config

        assert "chinook" in again.curated.datasources and again.curated.datasources["chinook"].verified_queries
        [source] = again.knowledge
        target = Path(source.path)
        assert target.is_file() and target.parent == tmp_path / "project" / "store"
        assert DEMO_GLOSSARY_PATH.parent not in target.parents
        from nl2sql.review.accept import _glossary_source

        assert Path(_glossary_source(engine._runtime).path) == target  # an accept writes here, in the project
        assert target.read_bytes() == DEMO_GLOSSARY_PATH.read_bytes()
        engine.add_demo()
        assert len(engine._runtime.config.knowledge) == 1


def test_add_demo_refuses_an_id_that_is_not_the_demos_database(tmp_path, monkeypatch):
    from nl2sql.common.errors import DatasourceExists
    from nl2sql.config import Datasource

    _no_build(monkeypatch)
    mine = Datasource(id="chinook", type="sqlite", connection={"database": str(tmp_path / "mine.db")})
    with _engine(tmp_path, datasources=[mine]) as engine:
        with pytest.raises(DatasourceExists, match="chinook"):
            engine.add_demo()
        assert [d.id for d in engine.datasources.list()] == ["chinook"]
        assert engine._runtime.config.knowledge == []


def test_add_demo_saves_the_added_datasources_where_the_config_keeps_them(tmp_path, monkeypatch):
    _no_build(monkeypatch)
    nl2sql.config.init_project(tmp_path / "p", demo=False, provider="replay")
    config_path = tmp_path / "p" / "nl2sql.yaml"
    from nl2sql.llm.replay import REPLAY
    from nl2sql.registry import Registry

    registry = Registry.default()
    registry.add_llm("replay", REPLAY)
    with nl2sql.NL2SQL(nl2sql.load_config(config_path), registry=registry) as engine:
        engine.add_demo(user_context=UserContext(user_id="a", roles=["admin"]))
        files = sorted(p.name for p in (tmp_path / "p" / "datasources").glob("*.yaml"))
        engine.add_demo()  # idempotent: nothing is saved twice

    assert {f"{i}.yaml" for i in DEMO_QUESTIONS_BY_DATASOURCE} <= set(files)
    with nl2sql.NL2SQL(nl2sql.load_config(config_path), registry=registry) as engine:
        assert set(DEMO_QUESTIONS_BY_DATASOURCE) <= {d.id for d in engine.datasources.list()}


def _project_engine(folder):
    """An engine loaded from a ``nl2sql.yaml`` in ``folder`` (``replay`` added by hand, as an install's entry point does)."""
    from nl2sql.llm.replay import REPLAY
    from nl2sql.registry import Registry

    registry = Registry.default()
    registry.add_llm("replay", REPLAY)
    return nl2sql.NL2SQL(nl2sql.load_config(folder / "nl2sql.yaml"), registry=registry)


def test_add_demo_persists_the_verified_queries_and_the_glossary_source(tmp_path, monkeypatch):
    _no_build(monkeypatch)
    project = tmp_path / "p"
    nl2sql.config.init_project(project, demo=False, provider="replay")
    with _project_engine(project) as engine:
        engine.add_demo()
    saved = {name: (project / name).read_bytes() for name in ("nl2sql.yaml", "curated.yaml", "knowledge.yaml")}

    with _project_engine(project) as engine:  # a restart
        config = engine._runtime.config
        assert config.curated.datasources["chinook"].verified_queries
        assert [Path(k.path).name for k in config.knowledge] == ["glossary.yaml", "demo-glossary.yaml"]
        assert (project / "demo-glossary.yaml").is_file()
        engine.add_demo()  # idempotent: no second copy of anything

    assert {name: (project / name).read_bytes() for name in saved} == saved
    with _project_engine(project) as engine:
        assert len(engine._runtime.config.knowledge) == 2


def test_add_demo_on_a_demo_project_accepts_its_own_copies_and_leaves_curated_alone(tmp_path, monkeypatch):
    _no_build(monkeypatch)
    nl2sql.config.init_project(tmp_path / "p", demo=True, provider="replay")  # has curated.yaml and data/*.db
    before = (tmp_path / "p" / "curated.yaml").read_bytes()
    with _project_engine(tmp_path / "p") as engine:
        engine.add_demo()  # the project's own demo databases are the demo's: no DatasourceExists
        assert len(engine._runtime.config.datasources) == 3
    assert (tmp_path / "p" / "curated.yaml").read_bytes() == before


def test_add_demo_on_a_code_built_engine_writes_no_project_file(tmp_path, monkeypatch):
    _no_build(monkeypatch)
    with _engine(tmp_path / "project") as engine:
        engine.add_demo()
        assert engine._runtime.config.curated.datasources["chinook"].verified_queries
    written = {p.name for p in (tmp_path / "project").rglob("*") if p.is_file()}
    assert "demo-glossary.yaml" in written and not written & {"curated.yaml", "nl2sql.yaml", "knowledge.yaml"}


def test_add_demo_refuses_a_users_own_copy_of_a_demo_database_with_the_same_file_name(tmp_path, monkeypatch):
    from nl2sql.common.errors import DatasourceExists
    from nl2sql.config import Datasource

    _no_build(monkeypatch)
    mine = Datasource(id="chinook", type="sqlite", connection={"database": str(tmp_path / "elsewhere" / "chinook.db")})
    with _engine(tmp_path, datasources=[mine]) as engine:
        with pytest.raises(DatasourceExists, match="chinook"):
            engine.add_demo()
        assert engine._runtime.config.knowledge == []


def test_datasources_list_and_get_show_only_what_the_caller_reaches(tmp_path):
    from nl2sql.common.errors import DatasourceNotFound
    from nl2sql.config import Datasource, Policies, Role

    one = Datasource(id="one", type="sqlite", connection={"database": str(tmp_path / "a.db")},
                     sample_questions=["How many?"])
    two = Datasource(id="two", type="sqlite", connection={"database": str(tmp_path / "b.db")})
    policies = Policies(roles={"a": Role(description="only one", allow=["one"]),
                               "admin": Role(description="all", allow=["*"])}, admin_roles=["admin"])
    with _engine(tmp_path, datasources=[one, two], policies=policies) as engine:
        reader = UserContext(user_id="r", roles=["a"])

        assert [d.id for d in engine.datasources.list()] == ["one", "two"]
        assert [d.id for d in engine.datasources.list(user_context=reader)] == ["one"]
        assert engine.datasources.get("one", user_context=reader).sample_questions == ["How many?"]
        with pytest.raises(DatasourceNotFound):
            engine.datasources.get("two", user_context=reader)
