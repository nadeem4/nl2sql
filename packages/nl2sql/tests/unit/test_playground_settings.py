"""The playground's settings panel: an API key and a model per LLM node.

Three rules, each tested here:

(a) settings work only on a loopback bind unless ``--allow-settings`` is given,
    because the playground has no login;
(b) the panel edits the demo project's own ``llm.demo.yaml`` and ``.env.demo``,
    the files the CLI reads, and nothing else;
(c) a saved key is write-only: no response carries it, only ``mask_key``'s form.
"""
import os
import re
import threading
import time

import pytest
import yaml

pytest.importorskip("fastapi")
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from nl2sql.api.query_api import QueryResult
from nl2sql.cli.common.api_key import VERIFIED_OPENAI_MODELS, mask_key
from nl2sql.cli.commands.demo import _key_from_env_file
from nl2sql.cli.demo.playground.app import build_app
from nl2sql.configs import ConfigManager
from nl2sql.llm import LLMRegistry
from nl2sql.secrets import SecretManager

# Built at run time so no scanner mistakes a test fixture for a leaked key.
FAKE_KEY = "-".join(["sk", "proj", "settingspanel" + "x" * 24 + "4f2a"])
FAKE_OPENROUTER_KEY = "-".join(["sk", "or", "v1", "settingspanel" + "y" * 24 + "9b1c"])
REPLAY_URL = "http://127.0.0.1:9/v1"

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _plain(text: str) -> str:
    return _ANSI.sub("", text)


@pytest.fixture(autouse=True)
def _replay_environment(monkeypatch):
    """What `nl2sql demo` leaves in the environment in replay mode."""
    monkeypatch.setenv("OPENAI_API_KEY", "replay")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_BASE", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)


@pytest.fixture
def project(tmp_path):
    """A demo project as `nl2sql demo` leaves it in replay mode."""
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "llm.demo.yaml").write_text(yaml.safe_dump({
        "version": 1,
        "default": {"provider": "openai", "model": "gpt-5.4",
                    "api_key": "${env:OPENAI_API_KEY}", "name": "default",
                    "base_url": REPLAY_URL},
        "agents": {},
    }, sort_keys=False), encoding="utf-8")
    (tmp_path / ".env.demo").write_text("ENV=demo\nOPENAI_API_KEY=\n", encoding="utf-8")
    return tmp_path


def _llm_yaml(project):
    return yaml.safe_load((project / "configs" / "llm.demo.yaml").read_text(encoding="utf-8"))


class _Context:
    def __init__(self, project):
        self.schema_store = None
        self.ds_registry = None
        self.llm_registry = LLMRegistry(SecretManager())
        cfg = ConfigManager().load_llm(project / "configs" / "llm.demo.yaml")
        agents = dict(cfg.agents or {})
        agents["default"] = cfg.default
        self.llm_registry.register_llms(agents)


class _Engine:
    """Runs no pipeline; reports which planner client a run would have used."""

    def __init__(self, project):
        self.context = _Context(project)
        self.used = []
        self.release = None
        self.entered = threading.Event()

    def run_query(self, natural_language, datasource_id=None, execute=True, user_context=None):
        self.entered.set()
        if self.release is not None:
            self.release.wait(5)
        self.used.append(self.context.llm_registry.get_llm("astplanner"))
        return QueryResult(status="success")


def _app(project, engine=None, host="127.0.0.1", allow_settings=False, mode="replay"):
    engine = engine or _Engine(project)
    app = build_app(engine, questions=["q1"], roles=["admin"], mode=mode, dataset="chinook",
                    trace_dir=project / "traces", project_dir=project, host=host,
                    allow_settings=allow_settings)
    # The Host header a browser on this machine sends.
    return engine, TestClient(app, base_url="http://127.0.0.1:8765")


# --- (a) local only by default ---------------------------------------------------


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.20", "::", "example.com"])
def test_settings_are_refused_on_a_non_loopback_host_without_the_flag(project, host):
    before_yaml = (project / "configs" / "llm.demo.yaml").read_text(encoding="utf-8")
    before_env = (project / ".env.demo").read_text(encoding="utf-8")
    _, client = _app(project, host=host)

    read = client.get("/api/settings").json()
    assert read["available"] is False
    assert "--allow-settings" in read["reason"]
    # Nothing about the configuration is disclosed when the panel is off.
    assert "models" not in read and "key" not in read

    assert client.post("/api/settings/key", json={"api_key": FAKE_KEY}).status_code == 403
    assert client.post("/api/settings/models",
                       json={"models": {"astplanner": "gpt-4.1"}}).status_code == 403

    assert (project / "configs" / "llm.demo.yaml").read_text(encoding="utf-8") == before_yaml
    assert (project / ".env.demo").read_text(encoding="utf-8") == before_env


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
    assert _key_from_env_file(project / ".env.demo") is None


# --- (c) secrets are write-only ----------------------------------------------------


def _every_route_response(client, trace_id="0b8f7d2e-1111-4222-8333-944455556666"):
    """One request per route; the test fails if a route is added and not listed."""
    requests = {
        ("GET", "/"): lambda: client.get("/"),
        ("GET", "/api/meta"): lambda: client.get("/api/meta"),
        ("GET", "/api/schema"): lambda: client.get("/api/schema"),
        ("POST", "/api/ask"): lambda: client.post("/api/ask", json={"question": "q1", "role": "admin"}),
        ("GET", "/api/trace/{trace_id}"): lambda: client.get(f"/api/trace/{trace_id}"),
        ("GET", "/api/settings"): lambda: client.get("/api/settings"),
        ("POST", "/api/settings/key"): lambda: client.post("/api/settings/key", json={"api_key": FAKE_KEY}),
        ("POST", "/api/settings/models"): lambda: client.post(
            "/api/settings/models", json={"models": {"astplanner": "gpt-4.1"}}),
        ("GET", "/api/index"): lambda: client.get("/api/index"),
        ("POST", "/api/index/rebuild"): lambda: client.post("/api/index/rebuild", json={"enrich": False}),
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
    (project / "traces" / f"20260921T000000Z_{trace_id}.json").write_text('{"trace_id": "x"}', encoding="utf-8")

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

    monkeypatch.setattr("nl2sql.cli.demo.playground.settings._persist_api_key", _boom)
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
    assert os.environ["OPENAI_API_KEY"] == FAKE_KEY
    # Persisted through the same path `--api-key` uses, into `.env.demo`.
    assert _key_from_env_file(project / ".env.demo") == FAKE_KEY
    assert _llm_yaml(project)["default"].get("base_url") is None
    # The key is referenced, never inlined, in the LLM config.
    assert FAKE_KEY not in (project / "configs" / "llm.demo.yaml").read_text(encoding="utf-8")


def test_an_openrouter_key_switches_to_openrouter(project):
    engine, client = _app(project)

    assert client.post("/api/settings/key", json={"api_key": FAKE_OPENROUTER_KEY}).status_code == 200

    assert _llm_yaml(project)["default"]["provider"] == "openrouter"
    assert _key_from_env_file(project / ".env.demo") == FAKE_OPENROUTER_KEY
    # The replay placeholder must not be sent to OpenRouter as a key.
    assert "OPENAI_API_KEY" not in os.environ
    llm = engine.context.llm_registry.get_llm("astplanner")
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


# --- (b) a model per node, in the same llm.demo.yaml -------------------------------


def test_a_node_model_is_written_to_the_llm_demo_yaml_the_cli_reads(project):
    engine, client = _app(project, mode="live")

    response = client.post("/api/settings/models", json={"models": {"astplanner": "gpt-4.1"}})

    assert response.status_code == 200
    # The CLI's own loader, on the file the CLI reads.
    cfg = ConfigManager().load_llm(project / "configs" / "llm.demo.yaml")
    assert cfg.agents["astplanner"].model == "gpt-4.1"
    assert cfg.agents["astplanner"].temperature == 0.0
    assert cfg.default.model == "gpt-5.4"
    # The node's endpoint and key reference follow the default's.
    assert cfg.agents["astplanner"].base_url == REPLAY_URL
    assert cfg.agents["astplanner"].api_key.get_secret_value() == "${env:OPENAI_API_KEY}"
    # And it takes effect without a restart.
    assert engine.context.llm_registry.get_llm("astplanner").model_name == "gpt-4.1"
    assert engine.context.llm_registry.get_llm("decomposer").model_name == "gpt-5.4"
    nodes = {n["agent"]: n["model"] for n in response.json()["nodes"]}
    assert nodes == {"datasourceresolver": None, "decomposer": None, "astplanner": "gpt-4.1",
                     "refiner": None, "answersynthesizer": None}


def test_choosing_a_model_that_rejects_temperature_writes_temperature_null(project):
    engine, client = _app(project)

    assert client.post("/api/settings/models",
                       json={"models": {"refiner": "gpt-5.5", "decomposer": "gpt-5-mini"}}).status_code == 200

    text = (project / "configs" / "llm.demo.yaml").read_text(encoding="utf-8")
    raw = yaml.safe_load(text)
    assert "temperature" in raw["agents"]["refiner"] and raw["agents"]["refiner"]["temperature"] is None
    assert raw["agents"]["decomposer"]["temperature"] is None
    assert engine.context.llm_registry.get_llm("refiner").temperature is None


def test_use_the_default_removes_the_node_entry(project):
    engine, client = _app(project)
    client.post("/api/settings/models", json={"models": {"astplanner": "gpt-4.1"}})

    assert client.post("/api/settings/models", json={"models": {"astplanner": None}}).status_code == 200

    assert "astplanner" not in (_llm_yaml(project).get("agents") or {})
    assert engine.context.llm_registry.get_llm("astplanner").model_name == "gpt-5.4"


def test_only_verified_models_are_offered_and_accepted(project):
    _, client = _app(project)
    before = (project / "configs" / "llm.demo.yaml").read_text(encoding="utf-8")

    offered = client.get("/api/settings").json()["models"]
    rejected = client.post("/api/settings/models", json={"models": {"astplanner": "gpt-3.5-turbo"}})
    unknown_node = client.post("/api/settings/models", json={"models": {"executor": "gpt-4.1"}})

    assert [m["id"] for m in offered] == list(VERIFIED_OPENAI_MODELS)
    assert {m["id"]: m["temperature"] for m in offered} == VERIFIED_OPENAI_MODELS
    assert rejected.status_code == 400 and unknown_node.status_code == 400
    assert (project / "configs" / "llm.demo.yaml").read_text(encoding="utf-8") == before


def test_the_verified_list_is_the_one_probed_on_2026_09_20():
    assert VERIFIED_OPENAI_MODELS == {
        "gpt-5.4": 0.0, "gpt-5.4-mini": 0.0, "gpt-4.1": 0.0, "gpt-4.1-mini": 0.0,
        "gpt-4o": 0.0, "gpt-5.5": None, "gpt-5-mini": None,
    }


def test_models_cannot_be_chosen_for_a_provider_without_a_verified_list(project):
    cfg = _llm_yaml(project)
    cfg["default"]["provider"] = "ollama"
    cfg["default"]["model"] = "llama3.1"
    (project / "configs" / "llm.demo.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    _, client = _app(project, mode="live")

    read = client.get("/api/settings").json()
    response = client.post("/api/settings/models", json={"models": {"astplanner": "gpt-4.1"}})

    assert read["models"] == [] and read["default_model"] == "llama3.1"
    assert response.status_code == 400


def test_the_settings_read_names_every_llm_node(project):
    _, client = _app(project)

    read = client.get("/api/settings").json()

    assert [n["agent"] for n in read["nodes"]] == ["datasourceresolver", "decomposer", "astplanner",
                                                   "refiner", "answersynthesizer"]
    assert all(n["label"] and n["model"] is None for n in read["nodes"])
    assert read["provider"] == "openai" and read["default_model"] == "gpt-5.4"
    assert read["key"]["masked"] is None
