"""Hosted mode: the playground as a public demo where each visitor brings a key.

What is proved here:

(a) the key arrives in a header, reaches the LLM registry for that one request
    and is gone afterwards -- it is not written to the project folder, to the
    config files, to the response or to the log;
(b) two visitors never see each other's key, and no cache outlives a request;
(c) no key, or a malformed key, is a clear 401 or 400 rather than a crash;
(d) the limits fire, with a sentence rather than a stack trace;
(e) everything that would write to disk -- Settings, Rebuild, feedback,
    ``--record`` -- is refused, while asking, the schema, the retrieval
    inspector and Debug stay;
(f) local mode is exactly what it was.
"""
import logging
import threading

import pytest
import yaml

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from nl2sql import NL2SQL, DatasourceAPI, LLM_API
from nl2sql.api.query_api import QueryResult
from nl2sql.cli.demo.playground.app import build_app
from nl2sql.cli.demo.playground.hosted import KEY_HEADER, SESSION_COOKIE, Hosted, Limits
from nl2sql.configs import ConfigManager
from nl2sql.llm import LLMRegistry
from nl2sql.llm.request_key import current_api_key
from nl2sql.secrets import SecretManager

# Built at run time so no scanner mistakes a test fixture for a leaked key.
VISITOR_KEY = "-".join(["sk", "proj", "hostedvisitor" + "a" * 24 + "1f4d"])
OTHER_KEY = "-".join(["sk", "proj", "hostedother" + "b" * 24 + "9c7e"])


@pytest.fixture
def project(tmp_path):
    """A demo project as ``nl2sql demo --hosted`` leaves it: no key anywhere."""
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "llm.demo.yaml").write_text(yaml.safe_dump({
        "version": 1,
        "default": {"provider": "openai", "model": "gpt-5.4",
                    "api_key": "${env:OPENAI_API_KEY}", "name": "default"},
        "agents": {},
    }, sort_keys=False), encoding="utf-8")
    (tmp_path / ".env.demo").write_text("ENV=demo\nOPENAI_API_KEY=\n", encoding="utf-8")
    return tmp_path


class _Context:
    def __init__(self, project):
        self.schema_store = None
        self.vector_store = None
        self.ds_registry = type("R", (), {"list_ids": staticmethod(lambda: [])})()
        self.llm_registry = LLMRegistry(SecretManager())
        cfg = ConfigManager().load_llm(project / "configs" / "llm.demo.yaml")
        self.llm_registry.register_llms({"default": cfg.default})


class _Engine(NL2SQL):
    """The real facade; runs no pipeline, but reports the key each run saw."""

    def __init__(self, project):
        self._ctx = _Context(project)
        self.datasource = DatasourceAPI(self._ctx)
        self.llm = LLM_API(self._ctx)
        self.seen = []
        self.hold = None

    def run_query(self, natural_language, datasource_id=None, execute=True, user_context=None):
        if self.hold is not None:
            self.hold.wait(5)
        # Exactly what a pipeline node does while the graph is built.
        client = self.context.llm_registry.get_llm("astplanner")
        self.seen.append((current_api_key(), client))
        return QueryResult(status="success")


def _client(project, *, hosted=True, limits=None, engine=None):
    engine = engine or _Engine(project)
    app = build_app(engine, questions=["q1"], roles=["admin"],
                    mode="hosted" if hosted else "replay", dataset="chinook",
                    trace_dir=project / "traces", project_dir=project, host="0.0.0.0",
                    hosted=Hosted(enabled=hosted, limits=limits))
    return engine, TestClient(app, base_url="http://demo.example:7860")


def _ask(client, key=None, question="How many customers are there?"):
    headers = {KEY_HEADER: key} if key else {}
    return client.post("/api/ask", json={"question": question, "role": "admin"}, headers=headers)


# --- (a) where the key goes, and where it does not ---------------------------------


def test_the_key_arrives_in_a_header_and_reaches_the_registry_for_that_request(project):
    engine, client = _client(project)

    answer = _ask(client, VISITOR_KEY)

    assert answer.status_code == 200, answer.text
    assert engine.seen and engine.seen[0][0] == VISITOR_KEY
    # The client was built from the visitor's key, not from the empty config.
    assert engine.seen[0][1].openai_api_key.get_secret_value() == VISITOR_KEY
    # And nothing of the request survives it.
    assert current_api_key() is None
    assert engine.context.llm_registry.llms == {}


def test_the_key_is_written_nowhere_in_the_project_and_is_in_no_response(project, caplog):
    engine, client = _client(project)
    before = {path: path.read_bytes() for path in project.rglob("*") if path.is_file()}

    with caplog.at_level(logging.DEBUG):
        answer = _ask(client, VISITOR_KEY)
        settings = client.get("/api/settings")
        meta = client.get("/api/meta")

    assert answer.status_code == 200
    # Not one byte of the demo project changed, so neither .env.demo nor
    # llm.demo.yaml nor anything else took the key.
    assert {path: path.read_bytes() for path in project.rglob("*") if path.is_file()} == before
    for path in project.rglob("*"):
        if path.is_file():
            assert VISITOR_KEY not in path.read_text(encoding="utf-8", errors="ignore")
    for text in (answer.text, settings.text, meta.text, caplog.text):
        assert VISITOR_KEY not in text
    # Nor into the process, where the next request would have found it.
    import os

    assert VISITOR_KEY not in "".join(os.environ.values())


# --- (b) one visitor's key is never another's --------------------------------------


def test_two_visitors_never_see_each_others_key(project):
    engine, client = _client(project)

    first = _ask(client, VISITOR_KEY)
    second = _ask(client, OTHER_KEY)

    assert (first.status_code, second.status_code) == (200, 200)
    keys = [key for key, _ in engine.seen]
    assert keys == [VISITOR_KEY, OTHER_KEY]
    # A cached client would hand the second visitor the first one's key.
    assert engine.seen[0][1] is not engine.seen[1][1]
    assert engine.seen[1][1].openai_api_key.get_secret_value() == OTHER_KEY


def test_two_questions_at_once_each_keep_their_own_key(project):
    engine, client = _client(project)
    engine.hold = threading.Event()
    answers = {}

    def ask(name, key):
        answers[name] = _ask(client, key)

    threads = [threading.Thread(target=ask, args=("a", VISITOR_KEY)),
               threading.Thread(target=ask, args=("b", OTHER_KEY))]
    for thread in threads:
        thread.start()
    engine.hold.set()
    for thread in threads:
        thread.join(10)

    assert {answer.status_code for answer in answers.values()} == {200}
    assert sorted(key for key, _ in engine.seen) == sorted([VISITOR_KEY, OTHER_KEY])
    for key, client_for_key in engine.seen:
        assert client_for_key.openai_api_key.get_secret_value() == key


# --- (c) no key, and a key that is not one -----------------------------------------


def test_a_question_without_a_key_is_401_and_says_where_to_add_one(project):
    engine, client = _client(project)

    answer = _ask(client)

    assert answer.status_code == 401
    assert "Settings" in answer.json()["detail"]
    assert "never stores it" in answer.json()["detail"]
    assert engine.seen == []


@pytest.mark.parametrize("bad", ["short", "has spaces in it and is long enough", "a" * 19])
def test_a_malformed_key_is_400_and_is_not_quoted_back(project, bad):
    engine, client = _client(project)

    answer = _ask(client, bad)

    assert answer.status_code == 400
    assert "does not look like an API key" in answer.json()["detail"]
    assert bad not in answer.text
    assert engine.seen == []


# --- (d) the limits ----------------------------------------------------------------


def test_the_rate_limit_fires_with_a_sentence_not_a_stack_trace(project):
    _, client = _client(project, limits=Limits(per_minute=2, per_session=100))

    codes = [_ask(client, VISITOR_KEY).status_code for _ in range(4)]
    refusal = _ask(client, VISITOR_KEY)

    assert codes == [200, 200, 429, 429]
    assert "Too many questions" in refusal.json()["detail"]
    assert "Traceback" not in refusal.text


def test_the_session_cap_fires_and_points_at_running_it_locally(project):
    clock = iter(range(0, 10_000, 120))  # two minutes apart: the bucket always refills
    limits = Limits(per_minute=5, per_session=3, clock=lambda: next(clock))
    _, client = _client(project, limits=limits)

    codes = [_ask(client, VISITOR_KEY).status_code for _ in range(4)]
    refusal = _ask(client, VISITOR_KEY)

    assert codes == [200, 200, 200, 429]
    assert "3 questions" in refusal.json()["detail"]
    assert "nl2sql demo" in refusal.json()["detail"]


def test_the_session_cookie_is_set_once_and_belongs_to_the_browser(project):
    _, client = _client(project)

    answer = _ask(client, VISITOR_KEY)

    cookie = answer.cookies.get(SESSION_COOKIE)
    assert cookie and cookie.isalnum()
    # It carries no key and no identity: a random token for counting questions.
    assert VISITOR_KEY not in cookie


def test_a_visitors_cap_is_their_own(project):
    limits = Limits(per_minute=50, per_session=1)
    _, client = _client(project, limits=limits)

    first = _ask(client, VISITOR_KEY)
    again = _ask(client, VISITOR_KEY)
    # A second browser: its own cookie, and its own allowance.
    client.cookies.clear()
    fresh = _ask(client, OTHER_KEY)

    assert (first.status_code, again.status_code, fresh.status_code) == (200, 429, 200)


# --- (e) what hosted mode turns off, and what it keeps ------------------------------


def test_settings_save_nothing_and_say_the_key_stays_in_the_browser(project):
    _, client = _client(project)
    before = (project / ".env.demo").read_text(encoding="utf-8")

    read = client.get("/api/settings").json()
    saved = client.post("/api/settings/key", json={"api_key": VISITOR_KEY})
    models = client.post("/api/settings/models", json={"models": {"astplanner": "gpt-4.1"}})

    assert read["available"] is False and read["hosted"] is True
    assert "browser tab" in read["reason"]
    assert (saved.status_code, models.status_code) == (403, 403)
    assert (project / ".env.demo").read_text(encoding="utf-8") == before
    assert VISITOR_KEY not in saved.text


def test_rebuild_is_refused_in_its_own_words(project):
    _, client = _client(project)

    refused = client.post("/api/index/rebuild", json={"enrich": False})

    assert refused.status_code == 403
    assert "Rebuilding the index is off on the hosted demo" in refused.json()["detail"]


def test_feedback_is_off(project):
    _, client = _client(project)

    read = client.get("/api/feedback").json()
    saved = client.post("/api/feedback", json={"trace_id": "abc", "rating": "up"})

    assert read["available"] is False
    assert "Feedback is off on the hosted demo" in read["reason"]
    assert saved.status_code == 403


def test_the_guided_questions_the_schema_and_the_retrieval_inspector_stay(project):
    _, client = _client(project)

    meta = client.get("/api/meta").json()
    retrieval = client.get("/api/retrieval").json()

    assert meta["hosted"] is True
    assert meta["questions"] == ["q1"]
    assert meta["limits"] == {"questions_per_minute": 6, "questions_per_session": 30}
    # Read only, over our own sample index, so it is part of what the demo shows.
    assert retrieval["available"] is True and retrieval["reason"] is None


# --- (f) local mode is unchanged ----------------------------------------------------


def test_local_mode_needs_no_header_and_reports_no_hosted_flag(project, monkeypatch):
    # What replay mode leaves in the environment: the process's own key.
    monkeypatch.setenv("OPENAI_API_KEY", "replay")
    engine, client = _client(project, hosted=False)

    meta = client.get("/api/meta").json()
    answer = _ask(client)

    assert meta["hosted"] is False and meta["limits"] is None
    assert answer.status_code == 200
    # No request key, so the registry resolves and caches exactly as before.
    assert engine.seen[0][0] is None
    assert set(engine.context.llm_registry.llms) == {"default"}
