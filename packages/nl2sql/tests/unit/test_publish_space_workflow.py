"""The workflow that deploys the hosted demo Space says what it deploys, and
where, and never fails a fork.

Nothing here runs the workflow -- it needs a Hugging Face write token and a real
Space. What is checkable from the repository is the drift that would only show
up after a merge: a trigger path that stops covering what the Space is built
from, a missing concurrency guard that lets two deploys race, a renamed secret,
or a Space id that quietly points somewhere else.
"""
import pathlib
import re

import pytest

yaml = pytest.importorskip("yaml")

ROOT = pathlib.Path(__file__).resolve().parents[4]
WORKFLOW = ROOT / ".github" / "workflows" / "publish_space.yml"
SPACE = ROOT / "deploy" / "huggingface"

SPACE_ID = "nadeem4nk/nl2sql-demo"

# The one line the mirror step rewrites to pin the image to the deployed
# commit. Written out here so a rename in either file fails a test rather than
# silently shipping a Space that still builds from `main`.
REF_ARG_ANCHOR = "ARG NL2SQL_REF="


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


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


def test_a_push_deploys_only_when_something_the_space_is_built_from_changed():
    push = _triggers(_workflow())["push"]

    assert push["branches"] == ["main"]
    # The Space root itself, the engine it installs, and the playground the
    # engine serves. A docs-only merge must not redeploy.
    for prefix in ("deploy/huggingface/**", "packages/nl2sql/**", "web/playground/**"):
        assert prefix in push["paths"]
    assert not any(path.startswith("docs/") for path in push["paths"])


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
    # Without this the mirror had nothing to commit whenever a deploy was
    # triggered by an engine or playground change -- the Space folder itself
    # had not moved -- so the Hub never rebuilt and the Space served whatever
    # `main` was the last time the folder happened to change.
    assert (SPACE / "SOURCE_SHA").exists()

    mirror = _step("Push deploy/huggingface")["run"]
    assert "SOURCE_SHA" in mirror
    assert "$GITHUB_SHA" in mirror


def test_the_mirror_pins_the_image_to_the_commit_it_is_deploying():
    """The rewrite has to keep matching the line it rewrites.

    If the `ARG` is renamed in the Dockerfile and the workflow is not, the
    deploy ships a Space that still builds `main` -- so replay the substitution
    here and insist it lands on exactly one line.
    """
    mirror = _step("Push deploy/huggingface")["run"]
    dockerfile = (SPACE / "Dockerfile").read_text(encoding="utf-8")

    assert REF_ARG_ANCHOR in mirror
    _, hits = re.subn(rf"(?m)^{REF_ARG_ANCHOR}.*$", f"{REF_ARG_ANCHOR}deadbeef", dockerfile)
    assert hits == 1

    # And the deploy fails loudly rather than silently if it ever stops landing.
    assert "grep -qx" in mirror


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
