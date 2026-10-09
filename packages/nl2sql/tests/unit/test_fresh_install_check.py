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
    "nl2sql_adapter_sdk-0.2.0-py3-none-any.whl",
    "nl2sql_api-0.2.0-py3-none-any.whl",
    "nl2sql_engine-0.2.0.tar.gz",
)


# --- which wheels, installed how ----------------------------------------------

def test_find_wheels_names_each_distribution(check, tmp_path):
    wheels = check.find_wheels(_dist(tmp_path, *WHEELS))
    assert wheels["engine"].name == "nl2sql_engine-0.2.0-py3-none-any.whl"
    assert wheels["adapter_sdk"].name == "nl2sql_adapter_sdk-0.2.0-py3-none-any.whl"
    assert wheels["api"].name == "nl2sql_api-0.2.0-py3-none-any.whl"


def test_find_wheels_does_not_confuse_api_with_adapter_sdk(check, tmp_path):
    # `nl2sql_a*` matches both; the stem has to be matched whole.
    wheels = check.find_wheels(_dist(tmp_path, "nl2sql_engine-1-py3-none-any.whl",
                                     "nl2sql_adapter_sdk-1-py3-none-any.whl"))
    assert "api" not in wheels


def test_find_wheels_refuses_a_dist_without_the_engine(check, tmp_path):
    with pytest.raises(SystemExit, match="nl2sql_engine"):
        check.find_wheels(_dist(tmp_path, "nl2sql_adapter_sdk-1-py3-none-any.whl"))


def test_find_wheels_refuses_two_versions_of_one_distribution(check, tmp_path):
    # A stale wheel left in dist/ would make the install pick one at random.
    with pytest.raises(SystemExit, match="more than one"):
        check.find_wheels(_dist(tmp_path, "nl2sql_engine-1-py3-none-any.whl",
                                "nl2sql_engine-2-py3-none-any.whl",
                                "nl2sql_adapter_sdk-1-py3-none-any.whl"))


def test_install_args_install_the_engine_with_the_demo_extra(check, tmp_path):
    dist = _dist(tmp_path, *WHEELS)
    args = check.wheel_install_args(check.find_wheels(dist), dist)
    engine = str(dist / "nl2sql_engine-0.2.0-py3-none-any.whl")
    assert f"{engine}[demo]" in args
    # The SDK is the engine's dependency at a version PyPI may not have yet,
    # so it comes from the same build, and so does anything else it finds.
    assert str(dist / "nl2sql_adapter_sdk-0.2.0-py3-none-any.whl") in args
    assert args[args.index("--find-links") + 1] == str(dist)


def test_install_args_leave_the_api_out(check, tmp_path):
    # A user installs the engine with its demo extra; the REST API is a
    # separate distribution they do not need for the playground.
    dist = _dist(tmp_path, *WHEELS)
    args = check.wheel_install_args(check.find_wheels(dist), dist)
    assert not any("nl2sql_api" in a for a in args)


# --- the environment the demo runs in -----------------------------------------

def test_demo_env_drops_every_api_key(check):
    env = check.demo_env({"PATH": "/bin", "OPENAI_API_KEY": "sk-x", "ANTHROPIC_API_KEY": "a",
                          "OPENROUTER_API_KEY": "o", "SOMETHING_API_KEY": "s"})
    assert env["PATH"] == "/bin"
    assert not [k for k in env if k.endswith("_API_KEY")]


def test_demo_env_embeds_locally(check):
    assert check.demo_env({})["EMBEDDING_PROVIDER"] == "local"


def test_demo_command_is_hosted_headless_and_on_the_given_port(check, tmp_path):
    cmd = check.demo_command(tmp_path / "nl2sql", tmp_path / "demo", 8765)
    assert cmd[0] == str(tmp_path / "nl2sql")
    assert cmd[1] == "demo"
    assert "--hosted" in cmd and "--no-browser" in cmd
    assert cmd[cmd.index("--port") + 1] == "8765"
    assert cmd[cmd.index("--host") + 1] == "127.0.0.1"
    assert cmd[cmd.index("--dir") + 1] == str(tmp_path / "demo")


def test_venv_executable_follows_the_platform(check, tmp_path):
    assert check.venv_executable(tmp_path, "nl2sql", windows=True) == tmp_path / "Scripts" / "nl2sql.exe"
    assert check.venv_executable(tmp_path, "nl2sql", windows=False) == tmp_path / "bin" / "nl2sql"


# --- what a healthy playground looks like -------------------------------------

def test_page_problems_accept_the_built_page(check):
    assert check.page_problems(200, '<!doctype html><div id="root"></div>') == []


def test_page_problems_reject_the_unbuilt_placeholder(check):
    body = '<div id="root">The playground page has not been built. Run npm</div>'
    assert check.page_problems(200, body)


def test_page_problems_reject_an_error_status(check):
    assert check.page_problems(500, '<div id="root"></div>')


def test_meta_problems_want_hosted_mode_and_datasources(check):
    assert check.meta_problems({"hosted": True, "mode": "hosted", "datasources": ["chinook"]}) == []
    assert check.meta_problems({"hosted": False, "mode": "replay", "datasources": ["chinook"]})
    assert check.meta_problems({"hosted": True, "mode": "hosted", "datasources": []})


def test_schema_problems_want_tables(check):
    assert check.schema_problems({"tables": [{"name": "Album"}]}) == []
    assert check.schema_problems({"tables": []})


def test_index_problems_want_an_ok_index(check):
    assert check.index_problems({"health": {"status": "ok"}}) == []
    assert check.index_problems({"health": {"status": "empty", "problems": ["no entries"]}})


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


def test_wait_until_up_gives_up_at_the_deadline(check):
    clock = _Clock()
    with pytest.raises(SystemExit, match="did not answer"):
        check.wait_until_up(lambda: False, alive=lambda: True, timeout=10,
                            clock=clock.time, sleep=clock.sleep, interval=1)
    assert clock.now >= 10
