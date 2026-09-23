"""The Hugging Face Space definition says what a Docker Space needs, and agrees
with the Dockerfile beside it.

Nobody can deploy the Space from here, so what is checkable is the drift: a
front-matter field that goes missing, or an `app_port` that stops matching the
port the container listens on, both fail the Space only after a push.
"""
import pathlib
import re

import pytest

yaml = pytest.importorskip("yaml")

SPACE = pathlib.Path(__file__).resolve().parents[4] / "deploy" / "huggingface"


def _front_matter() -> dict:
    text = (SPACE / "README.md").read_text(encoding="utf-8")
    assert text.startswith("---\n"), "a Docker Space reads its configuration from the README's front-matter"
    return yaml.safe_load(text.split("---\n", 2)[1])


def test_the_space_front_matter_declares_a_public_docker_space():
    front = _front_matter()

    assert front["sdk"] == "docker"
    assert front["app_port"] == 7860
    assert front["title"] and front["emoji"]
    assert front["colorFrom"] and front["colorTo"]
    assert front["pinned"] is False
    assert front["license"] == "mit"


def test_the_declared_port_is_the_one_the_container_listens_on():
    dockerfile = (SPACE / "Dockerfile").read_text(encoding="utf-8")
    compose = yaml.safe_load((SPACE / "docker-compose.yml").read_text(encoding="utf-8"))

    port = _front_matter()["app_port"]
    assert re.search(rf"^\s*PORT={port}\s*\\?$", dockerfile, re.MULTILINE)
    assert f"EXPOSE {port}" in dockerfile
    assert f"{port}:{port}" in compose["services"]["playground"]["ports"]


def test_the_container_runs_hosted_mode_as_a_non_root_user():
    dockerfile = (SPACE / "Dockerfile").read_text(encoding="utf-8")

    assert "NL2SQL_DEMO_HOSTED=1" in dockerfile
    assert "--hosted" in dockerfile
    assert "USER demo" in dockerfile
    assert "HEALTHCHECK" in dockerfile
    # The sample project and its index are built into the image, so the first
    # question does not wait for indexing or for a 79 MB model download.
    assert "nl2sql setup --demo" in dockerfile


def test_the_space_holds_no_api_key():
    for name in ("README.md", "Dockerfile", "docker-compose.yml"):
        text = (SPACE / name).read_text(encoding="utf-8")
        # No key, and no way to pass one in: the whole point of hosted mode.
        assert not re.search(r"(OPENAI|ANTHROPIC|OPENROUTER)_API_KEY\s*[:=]\s*\S", text), name
