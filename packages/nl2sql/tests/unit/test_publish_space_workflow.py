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
