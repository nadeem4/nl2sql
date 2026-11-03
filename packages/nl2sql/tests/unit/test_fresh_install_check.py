"""The logic in ``scripts/fresh_install_check.py``.

The script itself builds wheels, installs them into a clean virtualenv and
boots the demo, which is CI's job (the ``fresh-install`` job in test.yml). What
is tested here is everything that decides: which wheels to install and how,
which environment the demo runs in, and what counts as a healthy playground.
"""
import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[4] / "scripts" / "fresh_install_check.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("fresh_install_check", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def check():
    return _load_script()


def _dist(tmp_path, *names):
    for name in names:
        (tmp_path / name).write_bytes(b"")
    return tmp_path


WHEELS = (
    "nl2sql_engine-0.2.0-py3-none-any.whl",
    "nl2sql_sdk-0.2.0-py3-none-any.whl",
    "nl2sql_api-0.2.0-py3-none-any.whl",
    "nl2sql_engine-0.2.0.tar.gz",
)


# --- which wheels, installed how ----------------------------------------------

def test_find_wheels_names_each_distribution(check, tmp_path):
    wheels = check.find_wheels(_dist(tmp_path, *WHEELS))
    assert wheels["engine"].name == "nl2sql_engine-0.2.0-py3-none-any.whl"
    assert wheels["sdk"].name == "nl2sql_sdk-0.2.0-py3-none-any.whl"
    assert wheels["api"].name == "nl2sql_api-0.2.0-py3-none-any.whl"


def test_find_wheels_does_not_confuse_api_with_sdk(check, tmp_path):
    # `nl2sql_a*` matches both; the stem has to be matched whole.
    wheels = check.find_wheels(_dist(tmp_path, *WHEELS))
    assert wheels["api"].name.startswith("nl2sql_api-")
    assert wheels["sdk"].name.startswith("nl2sql_sdk-")


def test_find_wheels_refuses_a_dist_without_the_engine(check, tmp_path):
    with pytest.raises(SystemExit, match="nl2sql_engine"):
        check.find_wheels(_dist(tmp_path, "nl2sql_sdk-1-py3-none-any.whl"))


def test_find_wheels_refuses_two_versions_of_one_distribution(check, tmp_path):
    # A stale wheel left in dist/ would make the install pick one at random.
    with pytest.raises(SystemExit, match="more than one"):
        check.find_wheels(_dist(tmp_path, "nl2sql_engine-1-py3-none-any.whl",
                                "nl2sql_engine-2-py3-none-any.whl",
                                "nl2sql_sdk-1-py3-none-any.whl"))


def test_install_args_install_the_engine_with_the_demo_extra(check, tmp_path):
    dist = _dist(tmp_path, *WHEELS)
    args = check.wheel_install_args(check.find_wheels(dist), dist)
    engine = str(dist / "nl2sql_engine-0.2.0-py3-none-any.whl")
    assert f"{engine}[demo]" in args
    # The SDK is the engine's dependency at a version PyPI may not have yet,
    # so it comes from the same build, and so does anything else it finds.
    assert str(dist / "nl2sql_sdk-0.2.0-py3-none-any.whl") in args
    assert args[args.index("--find-links") + 1] == str(dist)


def test_install_args_name_the_api_wheel(check, tmp_path):
    # The demo extra is the web app, and the API is built at the same version
    # as the engine, which PyPI does not have until the release is published.
    dist = _dist(tmp_path, *WHEELS)
    args = check.wheel_install_args(check.find_wheels(dist), dist)
    assert str(dist / "nl2sql_api-0.2.0-py3-none-any.whl") in args


def test_find_wheels_refuses_a_dist_without_the_api(check, tmp_path):
    with pytest.raises(SystemExit, match="nl2sql_api"):
        check.find_wheels(_dist(tmp_path, "nl2sql_engine-1-py3-none-any.whl", "nl2sql_sdk-1-py3-none-any.whl"))


def test_wheel_version_reads_the_filename(check, tmp_path):
    assert check.wheel_version(tmp_path / "nl2sql_engine-0.2.0-py3-none-any.whl") == "0.2.0"


def test_pypi_install_args_pin_the_published_version_with_the_demo_extra(check):
    args = check.pypi_install_args("0.2.0")
    assert "nl2sql-engine[demo]==0.2.0" in args
    # A cached index page from before the upload is what makes a just-published
    # version look missing; nothing local may stand in for PyPI either.
    assert "--no-cache-dir" in args
    assert "--find-links" not in args


def test_retry_retries_until_success(check):
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise check.subprocess.CalledProcessError(1, "pip")

    check.retry(flaky, attempts=5, delay=0, sleep=lambda s: None)
    assert len(calls) == 3


def test_retry_gives_up_after_the_last_attempt(check):
    def broken():
        raise check.subprocess.CalledProcessError(1, "pip")

    with pytest.raises(check.subprocess.CalledProcessError):
        check.retry(broken, attempts=3, delay=0, sleep=lambda s: None)


# --- the environment the demo runs in -----------------------------------------

def test_demo_env_drops_every_api_key(check):
    env = check.demo_env({"PATH": "/bin", "OPENAI_API_KEY": "sk-x", "ANTHROPIC_API_KEY": "a",
                          "OPENROUTER_API_KEY": "o", "SOMETHING_API_KEY": "s"})
    assert env["PATH"] == "/bin"
    assert not [k for k in env if k.endswith("_API_KEY")]


def test_demo_env_embeds_locally(check):
    assert check.demo_env({})["EMBEDDING_PROVIDER"] == "local"


def test_demo_command_is_the_api_serving_the_hosted_demo_on_the_given_port(check, tmp_path):
    cmd = check.demo_command(tmp_path / "nl2sql-api", tmp_path / "demo", 8765)
    assert cmd[0] == str(tmp_path / "nl2sql-api")
    assert cmd[1:4] == ["serve", "--demo", "--hosted"]
    assert cmd[cmd.index("--port") + 1] == "8765"
    assert cmd[cmd.index("--host") + 1] == "127.0.0.1"
    assert cmd[cmd.index("--dir") + 1] == str(tmp_path / "demo")


def test_venv_executable_follows_the_platform(check, tmp_path):
    assert check.venv_executable(tmp_path, "nl2sql-api", windows=True) == tmp_path / "Scripts" / "nl2sql-api.exe"
    assert check.venv_executable(tmp_path, "nl2sql-api", windows=False) == tmp_path / "bin" / "nl2sql-api"


# --- what a healthy playground looks like -------------------------------------

def test_page_problems_accept_the_built_page(check):
    assert check.page_problems(200, '<!doctype html><div id="root"></div>') == []


def test_page_problems_reject_the_unbuilt_placeholder(check):
    body = '<div id="root">The playground page has not been built. Run npm</div>'
    assert check.page_problems(200, body)


def test_page_problems_reject_an_error_status(check):
    assert check.page_problems(500, '<div id="root"></div>')


HOSTED_META = {"mode": "hosted", "start": "hosted",
               "datasources": [{"id": "chinook", "sample_questions": ["a", "b"], "recorded": ["b"]}]}


def test_meta_problems_want_hosted_mode_and_datasources(check):
    assert check.meta_problems(HOSTED_META) == []
    assert check.meta_problems({**HOSTED_META, "mode": "demo", "start": None})
    assert check.meta_problems({**HOSTED_META, "datasources": []})


def test_meta_problems_want_a_recorded_question(check):
    unrecorded = {**HOSTED_META, "datasources": [{"id": "chinook", "sample_questions": ["a"], "recorded": []}]}
    assert check.meta_problems(unrecorded)


def test_recorded_questions_lists_what_the_keyless_demo_can_answer(check):
    assert check.recorded_questions(HOSTED_META) == [("chinook", "b")]
    assert check.recorded_questions({}) == []


def test_schema_problems_want_tables(check):
    assert check.schema_problems({"tables": [{"name": "Album"}]}) == []
    assert check.schema_problems({"tables": []})


def test_health_problems_want_the_expected_version_of_both_packages(check):
    ok = {"status": "ok", "version": {"engine": "0.2.0", "api": "0.2.0"}}
    assert check.health_problems(ok, "0.2.0") == []
    assert check.health_problems({"status": "ok", "version": {"engine": "0.1.2", "api": "0.2.0"}}, "0.2.0")
    assert check.health_problems({"status": "ok", "version": {"engine": "0.2.0", "api": "0.1.2"}}, "0.2.0")
    assert check.health_problems({**ok, "status": "starting"}, "0.2.0")


def test_index_problems_want_an_ok_index(check):
    assert check.index_problems({"status": "ok"}) == []
    assert check.index_problems({"status": "empty", "problems": ["no entries"]})


def test_turn_problems_want_an_answered_turn_with_a_reply(check):
    assert check.turn_problems({"status": "answered", "reply": "Rock."}, "a question") == []
    assert check.turn_problems({"status": "error", "error": {"code": "X"}}, "a question")
    assert check.turn_problems({"status": "answered", "reply": ""}, "a question")


# --- waiting for the server ---------------------------------------------------

class _Clock:
    def __init__(self):
        self.now = 0.0

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def test_wait_until_up_returns_once_the_url_answers(check):
    clock = _Clock()
    answers = iter([False, False, True])
    check.wait_until_up(lambda: next(answers), alive=lambda: True, timeout=60,
                        clock=clock.time, sleep=clock.sleep, interval=1)
    assert clock.now == 2


def test_wait_until_up_fails_when_the_server_dies(check):
    clock = _Clock()
    with pytest.raises(SystemExit, match="exited"):
        check.wait_until_up(lambda: False, alive=lambda: False, timeout=60,
                            clock=clock.time, sleep=clock.sleep, interval=1)


def _health(version):
    return {"status": "ok", "version": {"engine": version, "api": version}}


def test_wait_for_version_waits_out_the_old_build(check):
    # The Space keeps serving the previous image until the new one is up.
    clock = _Clock()
    seen = iter([None, _health("0.1.2"), _health("0.2.0")])
    check.wait_for_version(lambda: next(seen), "0.2.0", timeout=600,
                           clock=clock.time, sleep=clock.sleep, interval=30)
    assert clock.now == 60


def test_wait_for_version_names_what_it_last_saw(check):
    clock = _Clock()
    with pytest.raises(SystemExit, match="0.1.2"):
        check.wait_for_version(lambda: _health("0.1.2"), "0.2.0", timeout=60,
                               clock=clock.time, sleep=clock.sleep, interval=30)


def test_wait_until_up_gives_up_at_the_deadline(check):
    clock = _Clock()
    with pytest.raises(SystemExit, match="did not answer"):
        check.wait_until_up(lambda: False, alive=lambda: True, timeout=10,
                            clock=clock.time, sleep=clock.sleep, interval=1)
    assert clock.now >= 10


def test_the_follow_up_is_the_one_the_recorder_scripts(check):
    recorder = (SCRIPT.parent / "record_demo_answers.py").read_text(encoding="utf-8")
    assert f"{check.FOLLOW_UP[0]!r}".replace("'", '"') in recorder
    assert f"{check.FOLLOW_UP[1]!r}".replace("'", '"') in recorder


class _Scripted:
    """A stand-in client: records what it was sent and answers every turn."""

    def __init__(self, status="answered"):
        self.sent, self.status = [], status

    def post(self, path, body, timeout=0):
        self.sent.append(body.get("text", body))
        if path.endswith("/conversations"):
            return {"id": "c1"}
        return {"status": self.status, "reply": "An answer."}


def test_check_conversation_asks_each_recorded_question_and_the_scripted_follow_up(check):
    meta = {"datasources": [{"id": "chinook", "recorded": [check.FOLLOW_UP[0], "Other?"]}]}
    client = _Scripted()

    assert check.check_conversation(client, meta) == []
    asked = [m for m in client.sent if isinstance(m, str)]
    assert asked == [check.FOLLOW_UP[0], check.FOLLOW_UP[1], "Other?"]


def test_check_conversation_reports_a_turn_that_did_not_answer(check):
    meta = {"datasources": [{"id": "chinook", "recorded": ["Other?"]}]}

    assert check.check_conversation(_Scripted(status="error"), meta)


def test_a_post_waits_out_the_hosted_rate_limit_and_tries_again(check, monkeypatch):
    import io
    import urllib.error

    calls = []

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b'{"ok": true}'

    class _Opener:
        def open(self, request, timeout=None):
            calls.append(1)
            if len(calls) < 3:
                raise urllib.error.HTTPError(request.full_url, 429, "Too Many Requests", {"Retry-After": "7"}, io.BytesIO(b"{}"))
            return _Response()

    waits = []
    monkeypatch.setattr(check.time, "sleep", waits.append)
    client = check.Client("http://x")
    client.opener = _Opener()
    assert client.post("/p", {}) == {"ok": True}
    assert len(calls) == 3 and len(waits) == 2


class _Queries:
    """A stand-in client for /query and one turn: answers by question, or raises a 5xx."""

    def __init__(self, results, turn=None, fail_with=None):
        self.results, self.turn, self.fail_with, self.sent = results, turn, fail_with, []

    def post(self, path, body, timeout=0):
        import io
        import urllib.error

        if self.fail_with:
            raise urllib.error.HTTPError(path, self.fail_with, "boom", {}, io.BytesIO(b"{}"))
        self.sent.append(path)
        if path.endswith("/query"):
            return self.results[body["question"]]
        if path.endswith("/conversations"):
            return {"id": "c1"}
        return self.turn


_META = {"datasources": [{"id": "chinook", "recorded": ["a?", "b?"]}]}
_OK = {"status": "success"}


def test_check_queries_needs_one_answer_and_no_server_error(check):
    client = _Queries({"a?": {"status": "error", "errors": [{"code": "SECURITY_VIOLATION"}]}, "b?": _OK},
                      turn={"status": "error", "error": {"code": "REPLAY_MISS"}})

    assert check.check_queries(client, _META) == []


def test_check_queries_reports_when_nothing_was_answered(check):
    client = _Queries({"a?": {"status": "error"}, "b?": {"status": "error"}},
                      turn={"status": "error", "error": {"code": "REPLAY_MISS"}})

    assert any("answered none" in p for p in check.check_queries(client, _META))


def test_check_queries_reports_a_server_error_and_an_untyped_turn(check):
    assert any("500" in p for p in check.check_queries(_Queries({}, fail_with=500), _META))
    client = _Queries({"a?": _OK, "b?": _OK}, turn={"status": "error"})

    assert any("typed error" in p for p in check.check_queries(client, _META))


def test_guided_turns_are_checked_only_when_asked(check, monkeypatch):
    calls = []
    sessions = []
    monkeypatch.setattr(check, "check_conversation", lambda client, meta: calls.append("turns") or sessions.append(client) or [])
    monkeypatch.setattr(check, "check_queries", lambda client, meta: calls.append("queries") or sessions.append(client) or [])
    asking = check.Client("http://x")

    check.check_answers(asking, {}, guided_turns=False)
    check.check_answers(asking, {}, guided_turns=True)

    assert calls == ["queries", "queries", "turns"]
    # The guided turns use a session of their own: the queries already spent part of the per-session cap.
    assert sessions[0] is asking and sessions[1] is asking and sessions[2] is not asking
    assert sessions[2].base == asking.base


def test_a_session_cap_429_is_not_retried(check, monkeypatch):
    import io
    import urllib.error

    class _Opener:
        def open(self, request, timeout=None):
            raise urllib.error.HTTPError(request.full_url, 429, "Too Many Requests", {"Retry-After": "3600"}, io.BytesIO(b"{}"))

    waits = []
    monkeypatch.setattr(check.time, "sleep", waits.append)
    client = check.Client("http://x")
    client.opener = _Opener()

    with pytest.raises(urllib.error.HTTPError):
        client.post("/p", {})
    assert waits == []
