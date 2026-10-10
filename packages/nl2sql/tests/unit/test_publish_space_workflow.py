"""The workflow that deploys the hosted demo Space says what it deploys, and
where, and never fails a fork; and the release calls it.

Nothing here runs the workflow -- it needs a Hugging Face write token and a real
Space. What is checkable from the repository is the drift that would only show
up after a merge: a trigger path that stops covering what the Space is built
from, a release that stops reaching the Space, a missing concurrency guard that lets two deploys race, a renamed secret,
or a Space id that quietly points somewhere else.
"""
import pathlib
import re

import pytest

yaml = pytest.importorskip("yaml")

ROOT = pathlib.Path(__file__).resolve().parents[4]
WORKFLOW = ROOT / ".github" / "workflows" / "publish_space.yml"
PUBLISH = ROOT / ".github" / "workflows" / "publish_pypi.yaml"
RELEASE = ROOT / ".github" / "workflows" / "release_please.yml"
SPACE = ROOT / "deploy" / "huggingface"

SPACE_ID = "nadeem4nk/nl2sql-demo"

# The one line the mirror step rewrites to pin what the image installs.
# Written out here so a rename in either file fails a test rather than
# silently shipping a Space that still builds from `main`.
SPEC_ARG_ANCHOR = "ARG NL2SQL_SPEC="


def _workflow(path: pathlib.Path = WORKFLOW) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _step(name_fragment: str) -> dict:
    job = next(iter(_workflow()["jobs"].values()))
    for step in job["steps"]:
        if name_fragment in (step.get("name") or ""):
            return step
    raise AssertionError(f"no step named like {name_fragment!r}")


def _triggers(workflow: dict) -> dict:
    # PyYAML reads YAML 1.1, where a bare `on:` key is the boolean true.
    return workflow.get("on", workflow.get(True))


def test_the_workflow_is_valid_yaml_with_one_job():
    workflow = _workflow()

    assert workflow["name"]
    assert len(workflow["jobs"]) == 1


def test_it_can_be_run_by_hand_against_the_demo_space_by_default():
    dispatch = _triggers(_workflow())["workflow_dispatch"]

    assert dispatch["inputs"]["space_id"]["default"] == SPACE_ID


def test_a_push_to_main_never_deploys_the_public_space():
    # The public demo runs released code only; a merge reaches it with the
    # next release, not before.
    assert "push" not in _triggers(_workflow())


def test_a_release_calls_it_with_the_tag_and_learns_whether_it_deployed():
    call = _triggers(_workflow())["workflow_call"]

    assert call["inputs"]["tag"]["required"] is True
    assert call["inputs"]["space_id"]["default"] == SPACE_ID
    assert "deployed" in call["outputs"]


def test_a_hand_run_can_redeploy_a_release_by_tag():
    dispatch = _triggers(_workflow())["workflow_dispatch"]

    assert "tag" in dispatch["inputs"]
    assert dispatch["inputs"]["tag"]["required"] is False


def test_the_release_deploys_the_space_only_after_pypi_serves_the_version():
    jobs = _workflow(PUBLISH)["jobs"]

    assert jobs["pypi-smoke"]["needs"] == "pypi"
    assert "--pypi" in str(jobs["pypi-smoke"]["steps"])
    assert jobs["space"]["needs"] == "pypi-smoke"
    assert jobs["space"]["uses"] == "./.github/workflows/publish_space.yml"
    # HF_TOKEN reaches a called workflow only when it is handed down, at
    # every level: release_please -> publish_pypi -> publish_space.
    assert jobs["space"]["secrets"] == "inherit"
    assert _workflow(RELEASE)["jobs"]["publish"]["secrets"] == "inherit"


def test_the_release_checks_the_live_space_serves_the_new_version():
    smoke = _workflow(PUBLISH)["jobs"]["space-smoke"]

    assert smoke["needs"] == "space"
    # No token, no deploy: nothing to wait for.
    assert "needs.space.outputs.deployed == 'true'" in smoke["if"]
    run = str(smoke["steps"])
    assert "https://nadeem4nk-nl2sql-demo.hf.space" in run
    assert "--expect-version" in run


def test_one_deploy_at_a_time_and_a_superseded_run_is_cancelled():
    concurrency = _workflow()["concurrency"]

    assert concurrency["group"]
    assert concurrency["cancel-in-progress"] is True


def test_the_token_comes_from_the_hf_token_secret_and_the_job_skips_without_it():
    text = WORKFLOW.read_text(encoding="utf-8")
    job = next(iter(_workflow()["jobs"].values()))

    assert "HF_TOKEN" in text
    # Absent secret -> a logged line and a green job, never a failure. The
    # steps that talk to Hugging Face are gated on the check's output.
    assert any("present" in str(step.get("if", "")) for step in job["steps"])


def test_the_hub_client_is_pinned():
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "huggingface_hub==" in text


def test_every_deploy_writes_its_source_sha_into_the_space_root():
    assert (SPACE / "SOURCE_SHA").exists()

    mirror = _step("Push deploy/huggingface")["run"]
    assert "SOURCE_SHA" in mirror
    # The checked-out commit -- the tag's, on a release -- not the caller's.
    assert "rev-parse HEAD" in mirror


def test_a_release_pins_the_image_to_the_version_it_published():
    mirror = _step("Push deploy/huggingface")["run"]

    assert 'nl2sql-engine[demo]==${TAG#v}' in mirror


def test_a_release_waits_for_pypis_index_to_list_the_version_before_pushing():
    """v0.2.0's Space build ran minutes after the upload and pip saw only 0.1.x.

    The deploy waits until PyPI's simple index lists the version, bounded, and
    only for a tag: a dispatch with no tag installs from git, not PyPI.
    """
    job = next(iter(_workflow()["jobs"].values()))
    names = [step.get("name") or "" for step in job["steps"]]
    wait_at = next(i for i, name in enumerate(names) if "PyPI" in name and "index" in name)
    push_at = next(i for i, name in enumerate(names) if "Push deploy/huggingface" in name)
    wait = job["steps"][wait_at]

    assert wait_at < push_at
    assert "steps.token.outputs.present == 'true'" in wait["if"]
    assert "inputs.tag != ''" in wait["if"]
    run = wait["run"]
    assert "https://pypi.org/simple/nl2sql-engine/" in run
    assert "${TAG#v}" in run
    # Bounded, so a version that never appears fails the job instead of hanging it.
    assert re.search(r"seq 1 \d+", run)
    assert "exit 1" in run


def test_the_mirror_rewrites_exactly_one_line_and_checks_it_landed():
    """The rewrite has to keep matching the line it rewrites.

    If the `ARG` is renamed in the Dockerfile and the workflow is not, the
    deploy ships a Space that still builds `main` -- so replay the substitution
    here and insist it lands on exactly one line.
    """
    mirror = _step("Push deploy/huggingface")["run"]
    dockerfile = (SPACE / "Dockerfile").read_text(encoding="utf-8")

    assert SPEC_ARG_ANCHOR in mirror
    _, hits = re.subn(rf"(?m)^{SPEC_ARG_ANCHOR}.*$", 'ARG NL2SQL_SPEC="x"', dockerfile)
    assert hits == 1

    # And the deploy fails loudly rather than silently if it ever stops landing.
    assert "grep -qxF" in mirror


def test_the_wait_step_does_not_call_an_absent_rebuild_a_deploy():
    wait = _step("Wait for the Space")["run"]
    summary = _step("Summarise")["run"]

    # A run that pushed nothing triggered no build, so `RUNNING` says nothing
    # about this commit. Both steps have to know the difference.
    assert "PUSHED" in wait
    assert "PUSHED" in summary
    assert "No rebuild" in summary
    # The pushed commit is checked against the Space's head rather than assumed.
    assert "space_info" in wait


# --- The wait step's decisions, run against a fake Hub ----------------------
#
# A redeploy of v0.2.0 pushed nothing (the Space root was identical), so the
# Hub never rebuilt; the step logged that the Space was BUILD_ERROR and the job
# still went green. The logic stays inline in the workflow -- a dispatch with a
# tag checks out that tag, where a new script would not exist -- so these tests
# run the step's own Python with `huggingface_hub` replaced by a fake.

SPACE_SHA = "d6a6e92"


class _FakeHub:
    def __init__(self, stages: list[str]):
        self.stages = list(stages)
        self.restarts: list[dict] = []

    def stage(self) -> str:
        # The last stage repeats for as long as the step keeps asking.
        return self.stages.pop(0) if len(self.stages) > 1 else self.stages[0]


def _wait_script() -> str:
    run = _step("Wait for the Space")["run"]
    return run.split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]


def _run_wait(monkeypatch, tmp_path, *, pushed: bool, stages: list[str], factory_rebuild: str = "") -> tuple[int, dict, _FakeHub]:
    import sys
    import time
    import types

    hub = _FakeHub(stages)

    class HfApi:
        def get_space_runtime(self, repo_id):
            return types.SimpleNamespace(stage=hub.stage())

        def space_info(self, repo_id):
            return types.SimpleNamespace(sha=SPACE_SHA)

        def restart_space(self, repo_id, **kwargs):
            hub.restarts.append({"repo_id": repo_id, **kwargs})

        def fetch_space_logs(self, repo_id, build=False):
            return iter(["build log line\n"])

    monkeypatch.setitem(sys.modules, "huggingface_hub", types.SimpleNamespace(HfApi=HfApi))
    # A clock that moves only when the step sleeps, so the 5-minute grace and
    # the build timeout pass instantly.
    offset = [0.0]
    real_time = time.time
    monkeypatch.setattr(time, "time", lambda: real_time() + offset[0])
    monkeypatch.setattr(time, "sleep", lambda seconds: offset.__setitem__(0, offset[0] + seconds))

    out = tmp_path / "github_output"
    out.write_text("", encoding="utf-8")
    for key, value in {
        "SPACE_ID": SPACE_ID,
        "SOURCE_SHA": "abc123",
        "PUSHED": "true" if pushed else "false",
        "SPACE_COMMIT": SPACE_SHA,
        "SPACE_BUILD_TIMEOUT_SEC": "1800",
        "FACTORY_REBUILD": factory_rebuild,
        "GITHUB_OUTPUT": str(out),
    }.items():
        monkeypatch.setenv(key, value)

    code = 0
    try:
        exec(compile(_wait_script(), "wait-for-the-space", "exec"), {"__name__": "__main__"})
    except SystemExit as exc:
        code = exc.code or 0
    outputs = dict(line.split("=", 1) for line in out.read_text(encoding="utf-8").splitlines() if line)
    return code, outputs, hub


def test_an_unchanged_running_space_is_reported_deployed_without_a_rebuild(monkeypatch, tmp_path):
    code, outputs, hub = _run_wait(monkeypatch, tmp_path, pushed=False, stages=["RUNNING"])

    assert code == 0
    assert outputs.get("deployed") == "true"
    assert hub.restarts == []


@pytest.mark.parametrize("broken", ["BUILD_ERROR", "CONFIG_ERROR", "RUNTIME_ERROR", "NO_APP_FILE"])
def test_an_unchanged_broken_space_gets_a_factory_rebuild(monkeypatch, tmp_path, broken):
    code, outputs, hub = _run_wait(
        monkeypatch, tmp_path, pushed=False, stages=[broken, broken, "BUILDING", "APP_STARTING", "RUNNING"],
    )

    assert hub.restarts == [{"repo_id": SPACE_ID, "factory_reboot": True}]
    assert code == 0
    assert outputs.get("deployed") == "true"
    assert outputs.get("rebuilt") == "true"


def test_an_explicit_redeploy_always_rebuilds_even_a_running_space(monkeypatch, tmp_path):
    code, outputs, hub = _run_wait(
        monkeypatch, tmp_path, pushed=False, stages=["RUNNING", "BUILDING", "RUNNING"], factory_rebuild="true",
    )

    assert hub.restarts == [{"repo_id": SPACE_ID, "factory_reboot": True}]
    assert code == 0
    assert outputs.get("deployed") == "true"


def test_a_rebuild_that_fails_again_fails_the_job(monkeypatch, tmp_path):
    code, outputs, hub = _run_wait(
        monkeypatch, tmp_path, pushed=False, stages=["BUILD_ERROR", "BUILDING", "BUILD_ERROR"],
    )

    assert len(hub.restarts) == 1
    assert code == 1
    assert "deployed" not in outputs


def test_a_rebuild_that_never_starts_fails_the_job(monkeypatch, tmp_path):
    code, outputs, _ = _run_wait(monkeypatch, tmp_path, pushed=False, stages=["BUILD_ERROR"])

    assert code == 1
    assert "deployed" not in outputs


def test_a_pushed_build_that_comes_up_is_deployed_without_a_restart(monkeypatch, tmp_path):
    code, outputs, hub = _run_wait(monkeypatch, tmp_path, pushed=True, stages=["BUILDING", "RUNNING"])

    assert code == 0
    assert outputs.get("deployed") == "true"
    assert hub.restarts == []


def test_a_pushed_build_that_breaks_fails_the_job(monkeypatch, tmp_path):
    code, outputs, _ = _run_wait(monkeypatch, tmp_path, pushed=True, stages=["BUILDING", "BUILD_ERROR"])

    assert code == 1
    assert "deployed" not in outputs


@pytest.mark.parametrize("pushed", [True, False])
@pytest.mark.parametrize("final", ["BUILD_ERROR", "RUNTIME_ERROR", "PAUSED", "STOPPED", "BUILDING"])
def test_the_job_never_succeeds_unless_the_space_ends_running(monkeypatch, tmp_path, pushed, final):
    code, outputs, _ = _run_wait(monkeypatch, tmp_path, pushed=pushed, stages=["BUILDING", final])

    assert code == 1
    assert "deployed" not in outputs


def test_a_hand_run_asks_for_a_factory_rebuild_and_a_release_does_not():
    dispatch = _triggers(_workflow())["workflow_dispatch"]["inputs"]
    call = _triggers(_workflow())["workflow_call"]["inputs"]
    wait = _step("Wait for the Space")

    assert dispatch["factory_rebuild"]["default"] is True
    assert "factory_rebuild" not in call
    assert wait["env"]["FACTORY_REBUILD"] == "${{ inputs.factory_rebuild }}"


def test_nothing_in_the_workflow_prints_the_token():
    # `set -x` would trace the push URL the token is embedded in, and an `echo`
    # of it would put it in the log. Actions masks a registered secret, but the
    # workflow should not be relying on that.
    for line in WORKFLOW.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        assert "set -x" not in stripped, line
        if "HF_TOKEN" in stripped:
            assert not stripped.startswith(("echo ", "printf ")), line
