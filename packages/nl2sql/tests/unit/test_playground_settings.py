"""The playground's settings panel: an API key and a model per LLM node.

Three rules, each tested here:

(a) settings work only on a loopback bind unless ``--allow-settings`` is given,
    because the playground has no login;
(b) the panel edits the demo project's own ``llm.yaml`` (the ``llm`` section
    its ``nl2sql.yaml`` includes) and ``.env``, the files the CLI reads, and
    nothing else;
(c) a saved key is write-only: no response carries it, only ``mask_key``'s form.
"""
import os
import re
import threading
import time

import pytest
import yaml
from index_helpers import TestIndex  # same directory
from langchain_core.embeddings import FakeEmbeddings

pytest.importorskip("fastapi")
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from nl2sql.api.query_api import QueryResult
from nl2sql.execution.artifacts import ArtifactStore
from nl2sql.storage.local import LocalResults
from nl2sql.llm.providers import VERIFIED_OPENAI_MODELS, mask_key
from nl2sql.cli.commands.demo import _key_from_env_file
from nl2sql.cli.demo.playground.app import build_app
from nl2sql import NL2SQL, load_config
from nl2sql.llm import LLMRegistry
from nl2sql.policies import PolicyEngine
from nl2sql.registry import Registry
from nl2sql.storage.files import FilesTraces
from nl2sql.secrets import SecretManager
from nl2sql.testing.config import make_config

# Built at run time so no scanner mistakes a test fixture for a leaked key.
FAKE_KEY = "-".join(["sk", "proj", "settingspanel" + "x" * 24 + "4f2a"])
FAKE_OPENROUTER_KEY = "-".join(["sk", "or", "v1", "settingspanel" + "y" * 24 + "9b1c"])
FAKE_ANTHROPIC_KEY = "-".join(["sk", "ant", "api03", "settingspanel" + "z" * 24 + "7d3e"])
REPLAY_URL = "http://127.0.0.1:9/v1"

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _plain(text: str) -> str:
    return _ANSI.sub("", text)


@pytest.fixture(autouse=True)
def _replay_environment(monkeypatch):
    """What `nl2sql demo` leaves in the environment in replay mode: no key at all.

    The replay server's placeholder key is in ``llm.yaml``, not the environment.
    """
    for name in ("OPENAI_API_KEY", "OPENROUTER_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("OPENAI_API_BASE", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)


@pytest.fixture
def project(tmp_path):
    """A demo project as `nl2sql demo` leaves it in replay mode: nl2sql.yaml includes llm.yaml."""
    (tmp_path / "llm.yaml").write_text(yaml.safe_dump({
        "provider": "openai", "model": "gpt-5.4", "api_key": "replay", "base_url": REPLAY_URL,
    }, sort_keys=False), encoding="utf-8")
    make_config(tmp_path, llm={"file": "llm.yaml"})
    (tmp_path / ".env").write_text("OPENAI_API_KEY=\n", encoding="utf-8")
    return tmp_path


def _llm_yaml(project):
    return yaml.safe_load((project / "llm.yaml").read_text(encoding="utf-8"))


class _EmptyRegistry:
    def list_ids(self):
        return []

    def unavailable(self):
        return {}

    def get_preferred_tables(self, _ds):
        return []


class _Context:
    def __init__(self, project):
        # An empty index of a fake embedding model: never built, so health is "missing".
        index = TestIndex(project / ".nl2sql", FakeEmbeddings(size=8))
        for key, value in vars(index.ctx).items():
            setattr(self, key, value)
        self.vector_store = index.retriever()
        self.curated = None
        self.ds_registry = _EmptyRegistry()
        self.llm_registry = LLMRegistry(SecretManager())
        self.registry = Registry.default()
        self.traces = FilesTraces(project / "traces")
        self.config = load_config(project / "nl2sql.yaml")
        self.policy_engine = PolicyEngine(self.config.policies)
        self.replace_llm(self.config.llm)
        self.tenant_id = "default_tenant"
        self.artifact_store = ArtifactStore(LocalResults(str(project / "artifacts")), path_template="<tenant_id>/<request_id>/<sub_query_id>.parquet")


    def replace_llm(self, llm, **kwargs):
        """What the runtime's ``replace_llm`` does with the clients: build the new set, then swap it in."""
        self.llm_registry.swap(self.llm_registry.build_from(llm))


class _Engine(NL2SQL):
    """The real facade; runs no pipeline, reports which planner client a run would have used."""

    def __init__(self, project):
        self._runtime = _Context(project)
        self.used = []
        self.release = None
        self.entered = threading.Event()

    def run_query(self, natural_language, datasource_id=None, execute=True, user_context=None,
                  cancellation_token=None):
        self.entered.set()
        if self.release is not None:
            self.release.wait(5)
        self.used.append(self._runtime.llm_registry.get_llm("astplanner"))
        return QueryResult(status="success")


def _app(project, engine=None, host="127.0.0.1", allow_settings=False, mode="replay"):
    engine = engine or _Engine(project)
    app = build_app(engine, questions=["q1"], roles=["admin"], mode=mode, dataset="chinook",
                    project_dir=project, host=host,
                    allow_settings=allow_settings)
    # The Host header a browser on this machine sends.
    return engine, TestClient(app, base_url="http://127.0.0.1:8765")


# --- (a) local only by default ---------------------------------------------------


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.20", "::", "example.com"])
def test_settings_are_refused_on_a_non_loopback_host_without_the_flag(project, host):
    before_yaml = (project / "llm.yaml").read_text(encoding="utf-8")
    before_env = (project / ".env").read_text(encoding="utf-8")
    _, client = _app(project, host=host)

    read = client.get("/api/settings").json()
    assert read["available"] is False
    assert "--allow-settings" in read["reason"]
    # Nothing about the configuration is disclosed when the panel is off.
    assert "models" not in read and "key" not in read

    assert client.post("/api/settings/key", json={"api_key": FAKE_KEY}).status_code == 403
    assert client.post("/api/settings/models",
                       json={"models": {"astplanner": "gpt-4.1"}}).status_code == 403

    assert (project / "llm.yaml").read_text(encoding="utf-8") == before_yaml
    assert (project / ".env").read_text(encoding="utf-8") == before_env


def test_settings_are_allowed_on_a_non_loopback_host_with_the_flag(project):
    _, client = _app(project, host="0.0.0.0", allow_settings=True)

    assert client.get("/api/settings").json()["available"] is True
    assert client.post("/api/settings/models",
                       json={"models": {"astplanner": "gpt-4.1"}}).status_code == 200


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "127.0.0.2"])
def test_settings_are_allowed_on_a_loopback_host(project, host):
    _, client = _app(project, host=host)

    assert client.get("/api/settings").json()["available"] is True
    assert client.post("/api/settings/models",
                       json={"models": {"refiner": "gpt-4.1-mini"}}).status_code == 200


def test_settings_are_unavailable_without_a_demo_project(project):
    app = build_app(_Engine(project), questions=[], roles=["admin"], mode="live", dataset="chinook")
    client = TestClient(app)

    assert client.get("/api/settings").json()["available"] is False
    assert client.post("/api/settings/key", json={"api_key": FAKE_KEY}).status_code == 403


def test_a_cross_site_request_cannot_change_settings(project):
    """Another page open in the same browser can reach a loopback port.

    A form post from it carries that page's Origin; a DNS-rebound name carries
    a Host that is not a loopback name. Both are refused.
    """
    _, client = _app(project)

    foreign_origin = client.post("/api/settings/key", json={"api_key": FAKE_KEY},
                                 headers={"Origin": "https://evil.example"})
    rebound_host = client.post("/api/settings/key", json={"api_key": FAKE_KEY},
                               headers={"Host": "evil.example:8765"})
    not_json = client.post("/api/settings/key", content=f'{{"api_key": "{FAKE_KEY}"}}',
                           headers={"Content-Type": "text/plain"})

    assert foreign_origin.status_code == 403
    assert rebound_host.status_code == 403
    assert not_json.status_code in (403, 415)
    assert _key_from_env_file(project / ".env") is None


# --- (c) secrets are write-only ----------------------------------------------------


def _every_route_response(client, trace_id="0b8f7d2e-1111-4222-8333-944455556666"):
    """One request per route; the test fails if a route is added and not listed."""
    requests = {
        ("GET", "/"): lambda: client.get("/"),
        ("GET", "/social-card.png"): lambda: client.get("/social-card.png"),
        ("GET", "/clips/{name}"): lambda: client.get("/clips/pipeline.jpg"),
        ("GET", "/api/health"): lambda: client.get("/api/health"),
        ("GET", "/api/meta"): lambda: client.get("/api/meta"),
        ("GET", "/api/schema"): lambda: client.get("/api/schema"),
        ("POST", "/api/ask"): lambda: client.post("/api/ask", json={"question": "q1", "role": "admin"}),
        ("POST", "/api/ask/stream"): lambda: client.post("/api/ask/stream", json={"question": "q1", "role": "admin"}),
        ("GET", "/api/trace/{trace_id}"): lambda: client.get(f"/api/trace/{trace_id}"),
        ("GET", "/api/query/{request_id}/results/{result_id}"): lambda: client.get(
            f"/api/query/{trace_id}/results/combine_g1"),
        ("GET", "/api/query/{request_id}/results/{result_id}.csv"): lambda: client.get(
            f"/api/query/{trace_id}/results/combine_g1.csv"),
        ("GET", "/api/pipeline"): lambda: client.get("/api/pipeline"),
        ("GET", "/api/settings"): lambda: client.get("/api/settings"),
        ("POST", "/api/settings/key"): lambda: client.post("/api/settings/key", json={"api_key": FAKE_KEY}),
        ("POST", "/api/settings/models"): lambda: client.post(
            "/api/settings/models", json={"models": {"astplanner": "gpt-4.1"}}),
        ("GET", "/api/index"): lambda: client.get("/api/index"),
        ("POST", "/api/index/rebuild"): lambda: client.post("/api/index/rebuild", json={"enrich": False}),
        ("GET", "/api/retrieval"): lambda: client.get("/api/retrieval"),
        ("POST", "/api/retrieval"): lambda: client.post("/api/retrieval", json={"query": "q1"}),
        ("GET", "/api/feedback"): lambda: client.get("/api/feedback"),
        ("POST", "/api/feedback"): lambda: client.post(
            "/api/feedback", json={"trace_id": trace_id, "rating": "up", "note": "wrong number"}),
    }
    declared = {
        (method, route.path)
        for route in client.app.routes if isinstance(route, APIRoute)
        for method in route.methods
    }
    assert declared == set(requests), f"list every route here: {declared ^ set(requests)}"
    return {name: send() for name, send in requests.items()}


def _assert_no_key(text, key=FAKE_KEY):
    plain = _plain(text)
    assert key not in plain
    assert key not in "".join(plain.split())
    # Nor the part the mask hides.
    assert key[3:-4] not in plain


def test_a_saved_key_is_never_returned_by_any_route(project):
    engine, client = _app(project)
    saved = client.post("/api/settings/key", json={"api_key": FAKE_KEY})
    assert saved.status_code == 200
    # The trace route has something real to serve.
    (project / "traces").mkdir()
    trace_id = "0b8f7d2e-1111-4222-8333-944455556666"
    (project / "traces" / f"20260921T000000Z_{trace_id}.json").write_text(
        '{"trace_format_version": 1, "trace_id": "%s"}' % trace_id, encoding="utf-8")

    responses = _every_route_response(client, trace_id)

    assert responses[("GET", "/api/trace/{trace_id}")].status_code == 200
    for (method, path), response in responses.items():
        _assert_no_key(response.text)
        for name, value in response.headers.items():
            _assert_no_key(f"{name}: {value}")
    _assert_no_key(saved.text)

    read = responses[("GET", "/api/settings")].json()
    assert read["key"]["masked"] == mask_key(FAKE_KEY) == "sk-...4f2a"
    assert read["key"]["env_var"] == "OPENAI_API_KEY"


def test_a_rejected_key_is_not_echoed_in_the_error(project):
    _, client = _app(project)
    bad = FAKE_KEY + " trailing words"

    response = client.post("/api/settings/key", json={"api_key": bad})
    # FastAPI's own 422 echoes the offending input unless told not to.
    wrong_type = client.post("/api/settings/key", json={"api_key": [FAKE_KEY]})

    assert response.status_code == 400
    _assert_no_key(response.text, key=bad)
    _assert_no_key(response.text)
    assert wrong_type.status_code == 422
    _assert_no_key(wrong_type.text)


def test_a_failed_save_does_not_echo_the_key(project, monkeypatch):
    def _boom(path, key):
        raise OSError(f"cannot write {key} to {path}")

    monkeypatch.setattr("nl2sql.cli.demo.playground.settings.persist_api_key", _boom)
    _, client = _app(project)

    response = client.post("/api/settings/key", json={"api_key": FAKE_KEY})

    assert response.status_code == 500
    _assert_no_key(response.text)


# --- the key switches replay to live without a restart -----------------------------


def test_saving_a_key_switches_replay_to_live_without_a_restart(project):
    engine, client = _app(project)
    assert client.get("/api/meta").json()["mode"] == "replay"
    client.post("/api/ask", json={"question": "q1", "role": "admin"})
    replay_client = engine.used[-1]
    assert replay_client.openai_api_base == REPLAY_URL

    assert client.post("/api/settings/key", json={"api_key": FAKE_KEY}).status_code == 200

    assert client.get("/api/meta").json()["mode"] == "live"
    client.post("/api/ask", json={"question": "q1", "role": "admin"})
    live_client = engine.used[-1]
    assert live_client is not replay_client
    assert live_client.openai_api_base in (None, "")
    assert live_client.openai_api_key.get_secret_value() == FAKE_KEY
    # The key reaches the engine as a value; the environment is never written.
    assert "OPENAI_API_KEY" not in os.environ
    # Persisted through the same path `--api-key` uses, into `.env`.
    assert _key_from_env_file(project / ".env") == FAKE_KEY
    assert _llm_yaml(project).get("base_url") is None and _llm_yaml(project).get("api_key") is None
    # The key is referenced, never inlined, in the LLM config.
    assert FAKE_KEY not in (project / "llm.yaml").read_text(encoding="utf-8")


def test_an_openrouter_key_switches_to_openrouter(project):
    engine, client = _app(project)

    assert client.post("/api/settings/key", json={"api_key": FAKE_OPENROUTER_KEY}).status_code == 200

    assert _llm_yaml(project)["provider"] == "openrouter"
    assert _key_from_env_file(project / ".env") == FAKE_OPENROUTER_KEY
    # The replay placeholder must not be sent to OpenRouter as a key.
    assert "OPENAI_API_KEY" not in os.environ
    llm = engine._runtime.llm_registry.get_llm("astplanner")
    assert llm.openai_api_key.get_secret_value() == FAKE_OPENROUTER_KEY
    read = client.get("/api/settings").json()
    assert read["key"]["env_var"] == "OPENROUTER_API_KEY"
    assert read["models"] == []
    assert "OpenAI" in read["models_note"]


def test_a_save_waits_for_a_run_in_flight(project):
    """The clients are swapped between runs, never under one."""
    engine = _Engine(project)
    engine.release = threading.Event()
    _, client = _app(project, engine=engine)
    finished = {}

    asking = threading.Thread(target=lambda: client.post("/api/ask", json={"question": "q1"}))
    asking.start()
    assert engine.entered.wait(5)

    saving = threading.Thread(target=lambda: finished.update(
        at=time.monotonic(), status=client.post("/api/settings/key", json={"api_key": FAKE_KEY}).status_code))
    saving.start()
    time.sleep(0.3)
    assert "at" not in finished, "the save went ahead under a running question"

    engine.release.set()
    asking.join(5)
    saving.join(5)
    assert finished["status"] == 200
    # The run in flight finished on the client it started with.
    assert engine.used[0].openai_api_base == REPLAY_URL


# --- (b) a model per node, in the same llm.yaml -------------------------------------


def test_a_node_model_is_written_to_the_llm_yaml_the_cli_reads(project):
    engine, client = _app(project, mode="live")

    response = client.post("/api/settings/models", json={"models": {"astplanner": "gpt-4.1"}})

    assert response.status_code == 200
    # The CLI's own loader, on the config the CLI reads.
    llm = load_config(project / "nl2sql.yaml").llm
    assert llm.agents["ast_planner"].model == "gpt-4.1"
    assert llm.agents["ast_planner"].temperature == 0.0
    assert llm.model == "gpt-5.4"
    # The node's endpoint and key follow the default's.
    assert llm.agents["ast_planner"].base_url == REPLAY_URL
    assert llm.agents["ast_planner"].api_key == "replay"
    # And it takes effect without a restart.
    assert engine._runtime.llm_registry.get_llm("astplanner").model_name == "gpt-4.1"
    assert engine._runtime.llm_registry.get_llm("decomposer").model_name == "gpt-5.4"
    nodes = {n["agent"]: n["model"] for n in response.json()["nodes"]}
    assert nodes == {"curatedmatcher": None, "datasourceresolver": None, "decomposer": None, "astplanner": "gpt-4.1",
                     "refiner": None, "answersynthesizer": None}


def test_choosing_a_model_that_rejects_temperature_writes_temperature_null(project):
    engine, client = _app(project)

    assert client.post("/api/settings/models",
                       json={"models": {"refiner": "gpt-5.5", "decomposer": "gpt-5-mini"}}).status_code == 200

    text = (project / "llm.yaml").read_text(encoding="utf-8")
    raw = yaml.safe_load(text)
    assert "temperature" in raw["agents"]["refiner"] and raw["agents"]["refiner"]["temperature"] is None
    assert raw["agents"]["decomposer"]["temperature"] is None
    assert engine._runtime.llm_registry.get_llm("refiner").temperature is None


def test_use_the_default_removes_the_node_entry(project):
    engine, client = _app(project)
    client.post("/api/settings/models", json={"models": {"astplanner": "gpt-4.1"}})

    assert client.post("/api/settings/models", json={"models": {"astplanner": None}}).status_code == 200

    assert "ast_planner" not in (_llm_yaml(project).get("agents") or {})
    assert engine._runtime.llm_registry.get_llm("astplanner").model_name == "gpt-5.4"


def test_only_verified_models_are_offered_and_accepted(project):
    _, client = _app(project)
    before = (project / "llm.yaml").read_text(encoding="utf-8")

    offered = client.get("/api/settings").json()["models"]
    rejected = client.post("/api/settings/models", json={"models": {"astplanner": "gpt-3.5-turbo"}})
    unknown_node = client.post("/api/settings/models", json={"models": {"executor": "gpt-4.1"}})

    assert [m["id"] for m in offered] == list(VERIFIED_OPENAI_MODELS)
    assert {m["id"]: m["temperature"] for m in offered} == VERIFIED_OPENAI_MODELS
    assert rejected.status_code == 400 and unknown_node.status_code == 400
    assert (project / "llm.yaml").read_text(encoding="utf-8") == before


def test_the_verified_list_is_the_one_probed_on_2026_09_20():
    assert VERIFIED_OPENAI_MODELS == {
        "gpt-5.4": 0.0, "gpt-5.4-mini": 0.0, "gpt-4.1": 0.0, "gpt-4.1-mini": 0.0,
        "gpt-4o": 0.0, "gpt-5.5": None, "gpt-5-mini": None,
    }


def test_models_cannot_be_chosen_for_a_provider_without_a_verified_list(project):
    cfg = _llm_yaml(project)
    cfg["provider"] = "ollama"
    cfg["model"] = "llama3.1"
    (project / "llm.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    _, client = _app(project, mode="live")

    read = client.get("/api/settings").json()
    response = client.post("/api/settings/models", json={"models": {"astplanner": "gpt-4.1"}})

    assert read["models"] == [] and read["default_model"] == "llama3.1"
    assert response.status_code == 400


# --- a provider per node ---------------------------------------------------------


def _live_on_openai(project, monkeypatch, anthropic_key=True):
    """A live demo on OpenAI, with an Anthropic key saved too (or not)."""
    cfg = _llm_yaml(project)
    cfg.pop("base_url")
    cfg.pop("api_key")
    (project / "llm.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    if anthropic_key:
        monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_ANTHROPIC_KEY)
    else:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return _app(project, mode="live")


def test_a_node_can_be_put_on_another_provider(project, monkeypatch):
    pytest.importorskip("langchain_anthropic")
    engine, client = _live_on_openai(project, monkeypatch)

    response = client.post("/api/settings/models", json={
        "models": {"astplanner": {"provider": "anthropic", "model": "claude-opus-5"}}})

    assert response.status_code == 200, response.text
    entry = _llm_yaml(project)["agents"]["ast_planner"]
    # The node's own provider and temperature; no key named, so ANTHROPIC_API_KEY is read.
    assert entry["provider"] == "anthropic" and entry["model"] == "claude-opus-5"
    assert "api_key" not in entry
    assert entry["temperature"] is None and "base_url" not in entry
    assert _llm_yaml(project)["provider"] == "openai"
    # Applied without a restart, through the anthropic wire.
    assert engine._runtime.llm_registry.get_llm("astplanner")._llm_type == "anthropic-chat"
    assert engine._runtime.llm_registry.get_llm("decomposer")._llm_type == "openai-chat"
    node = {n["agent"]: n for n in response.json()["nodes"]}["astplanner"]
    assert (node["provider"], node["model"], node["unavailable"]) == ("anthropic", "claude-opus-5", None)
    _assert_no_key(response.text, FAKE_ANTHROPIC_KEY)


def test_the_read_lists_every_provider_with_its_own_masked_key(project, monkeypatch):
    _, client = _live_on_openai(project, monkeypatch)

    providers = {p["id"]: p for p in client.get("/api/settings").json()["providers"]}

    assert set(providers) == {"openai", "anthropic"}
    assert providers["anthropic"]["masked"] == mask_key(FAKE_ANTHROPIC_KEY)
    assert providers["anthropic"]["env_var"] == "ANTHROPIC_API_KEY"
    assert providers["anthropic"]["usable"] is True
    assert [m["id"] for m in providers["anthropic"]["models"]] == ["claude-opus-5", "claude-sonnet-5",
                                                                 "claude-haiku-4-5"]


def test_a_provider_without_a_key_cannot_be_chosen(project, monkeypatch):
    _, client = _live_on_openai(project, monkeypatch, anthropic_key=False)
    before = (project / "llm.yaml").read_text(encoding="utf-8")

    providers = {p["id"]: p for p in client.get("/api/settings").json()["providers"]}
    response = client.post("/api/settings/models", json={
        "models": {"astplanner": {"provider": "anthropic", "model": "claude-opus-5"}}})

    assert providers["anthropic"]["usable"] is False
    assert response.status_code == 400
    assert "key for Anthropic" in response.json()["detail"]
    assert (project / "llm.yaml").read_text(encoding="utf-8") == before


def test_a_node_whose_provider_has_no_key_is_shown_unavailable(project, monkeypatch):
    cfg = _llm_yaml(project)
    cfg["agents"] = {"ast_planner": {"provider": "anthropic", "model": "claude-opus-5", "temperature": None}}
    (project / "llm.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    _, client = _live_on_openai(project, monkeypatch, anthropic_key=False)

    nodes = {n["agent"]: n for n in client.get("/api/settings").json()["nodes"]}

    assert "Anthropic has no key" in nodes["astplanner"]["unavailable"]
    assert nodes["decomposer"]["unavailable"] is None


def test_a_second_key_keeps_the_first_and_the_nodes_on_its_provider(project, monkeypatch):
    pytest.importorskip("langchain_anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    engine, client = _app(project)
    assert client.post("/api/settings/key", json={"api_key": FAKE_KEY}).status_code == 200
    assert client.post("/api/settings/key", json={"api_key": FAKE_ANTHROPIC_KEY}).status_code == 200
    assert client.post("/api/settings/models", json={"models": {"astplanner": "claude-sonnet-5",
                                                                  "decomposer": {"provider": "openai",
                                                                                 "model": "gpt-4.1"}}}).status_code == 200

    # One key per provider, in .env and in the engine; never in the environment.
    assert "OPENAI_API_KEY" not in os.environ and "ANTHROPIC_API_KEY" not in os.environ
    assert engine._runtime.llm_registry.get_llm("astplanner").anthropic_api_key.get_secret_value()         == FAKE_ANTHROPIC_KEY
    env_demo = (project / ".env").read_text(encoding="utf-8")
    assert f"OPENAI_API_KEY={FAKE_KEY}" in env_demo and f"ANTHROPIC_API_KEY={FAKE_ANTHROPIC_KEY}" in env_demo
    cfg = _llm_yaml(project)
    # The default moved to the provider of the last key saved; the OpenAI step stayed.
    assert cfg["provider"] == "anthropic"
    assert cfg["agents"]["ast_planner"]["provider"] == "anthropic"
    assert cfg["agents"]["decomposer"] == {"provider": "openai", "model": "gpt-4.1", "temperature": 0.0}
    assert engine._runtime.llm_registry.get_llm("decomposer").openai_api_key.get_secret_value() == FAKE_KEY

    # Saving the OpenAI key again moves the default back; the Claude step stays on Claude.
    assert client.post("/api/settings/key", json={"api_key": FAKE_KEY}).status_code == 200
    cfg = _llm_yaml(project)
    assert cfg["provider"] == "openai"
    assert cfg["agents"]["ast_planner"]["provider"] == "anthropic"


def test_the_settings_read_names_every_llm_node(project):
    _, client = _app(project)

    read = client.get("/api/settings").json()

    assert [n["agent"] for n in read["nodes"]] == ["curatedmatcher", "datasourceresolver", "decomposer",
                                                   "astplanner", "refiner", "answersynthesizer"]
    assert all(n["label"] and n["model"] is None for n in read["nodes"])
    assert read["provider"] == "openai" and read["default_model"] == "gpt-5.4"
    assert read["key"]["masked"] is None


# --- a key the demo is given travels in the LLM config, never the environment ----


def test_with_api_keys_gives_each_entry_its_own_provider_s_key():
    from nl2sql.cli.demo.llm_config import with_api_keys
    from nl2sql.config import LLM
    from nl2sql.llm.registry import agents_from_config

    llm = LLM(provider="openai", model="gpt-5.4", agents={
        "ast_planner": {"provider": "anthropic", "model": "claude-opus-5"},
        "refiner": {"model": "gpt-4.1"},
        "decomposer": {"provider": "openrouter", "model": "x"},
        "answer_synthesizer": {"provider": "anthropic", "model": "claude-opus-5", "api_key": "${env:MINE}"},
    })

    agents = agents_from_config(with_api_keys(llm, {"openai": FAKE_KEY, "anthropic": FAKE_ANTHROPIC_KEY}))

    assert agents["default"].api_key.get_secret_value() == FAKE_KEY
    assert agents["refiner"].api_key.get_secret_value() == FAKE_KEY
    assert agents["astplanner"].api_key.get_secret_value() == FAKE_ANTHROPIC_KEY
    # No key given for OpenRouter: its own variable is read, never OpenAI's key.
    assert agents["decomposer"].api_key is None
    # A key the config names itself stays.
    assert agents["answersynthesizer"].api_key.get_secret_value() == "${env:MINE}"


def test_with_api_keys_keeps_a_key_the_config_names():
    from nl2sql.cli.demo.llm_config import with_api_keys
    from nl2sql.config import LLM

    llm = LLM(provider="openai", model="gpt-5.4", api_key="replay", base_url=REPLAY_URL)

    assert with_api_keys(llm, {"openai": FAKE_KEY}).api_key == "replay"
    assert with_api_keys(llm, {}) is llm


def test_a_key_the_demo_was_given_shows_masked_in_the_panel(project):
    cfg = _llm_yaml(project)
    cfg.pop("base_url")
    cfg.pop("api_key")
    (project / "llm.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    app = build_app(_Engine(project), questions=[], roles=["admin"], mode="live", dataset="chinook",
                    project_dir=project, host="127.0.0.1", api_keys={"openai": FAKE_KEY})

    read = TestClient(app, base_url="http://127.0.0.1:8765").get("/api/settings").json()

    assert read["key"] == {"masked": mask_key(FAKE_KEY), "env_var": "OPENAI_API_KEY"}
    assert "OPENAI_API_KEY" not in os.environ
