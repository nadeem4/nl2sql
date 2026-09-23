"""The workflow that deploys the hosted demo Space says what it deploys, and
where, and never fails a fork.

Nothing here runs the workflow -- it needs a Hugging Face write token and a real
Space. What is checkable from the repository is the drift that would only show
up after a merge: a trigger path that stops covering what the Space is built
from, a missing concurrency guard that lets two deploys race, a renamed secret,
or a Space id that quietly points somewhere else.
"""
import pathlib

import pytest

yaml = pytest.importorskip("yaml")

WORKFLOW = (
    pathlib.Path(__file__).resolve().parents[4]
    / ".github"
    / "workflows"
    / "publish_space.yml"
)

SPACE_ID = "nadeem4nk/nl2sql-demo"


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


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
