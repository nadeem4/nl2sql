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


def test_the_space_card_has_a_sentence_and_a_thumbnail():
    """What the Space's own link preview is made of.

    A Space card shows the `short_description` and the `thumbnail`, and has
    nothing else to show: the README's body is the page, not the card. The
    thumbnail is read from raw GitHub rather than from the Space, so the card
    works before the Space has built and while it is sleeping.
    """
    front = _front_matter()

    assert front["short_description"]
    assert len(front["short_description"]) <= 60, "Hugging Face truncates a longer one"
    assert front["thumbnail"] == (
        "https://raw.githubusercontent.com/nadeem4/nl2sql/main/docs/assets/social-card.png"
    )


def test_the_thumbnail_names_a_card_this_repository_holds():
    card = SPACE.parents[1] / "docs" / "assets" / "social-card.png"

    assert card.is_file(), "deploy/huggingface/README.md points its thumbnail at a missing file"
    assert card.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


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


def test_the_engine_is_installed_from_whichever_commit_is_being_deployed():
    """The image must not install `main` at build time.

    It did, which meant a Space rebuilt for one reason picked up whatever `main`
    happened to be, and a Space that was never rebuilt stayed frozen. The ref is
    a build argument, declared before the install so rewriting it both pins the
    engine and busts the cache for every layer below.
    """
    lines = (SPACE / "Dockerfile").read_text(encoding="utf-8").splitlines()

    ref = next(i for i, line in enumerate(lines) if line.startswith("ARG NL2SQL_REF="))
    spec = next(i for i, line in enumerate(lines) if line.startswith("ARG NL2SQL_SPEC="))
    install = next(i for i, line in enumerate(lines) if "pip install" in line and "NL2SQL_SPEC" in line)

    assert ref < spec < install
    assert "${NL2SQL_REF}" in lines[spec]
    # The sha is enough on its own: no `refs/heads/` in the URL, or a commit
    # sha would not resolve.
    assert "refs/heads" not in lines[spec]


def test_the_source_sha_file_records_what_the_space_was_built_from():
    sha = (SPACE / "SOURCE_SHA").read_text(encoding="utf-8").strip()

    # In the repository it is the branch name; the publish workflow overwrites
    # it with the deploying commit in the copy it pushes to the Space.
    assert sha
    assert "\n" not in sha


def test_the_space_holds_no_api_key():
    for name in ("README.md", "Dockerfile", "docker-compose.yml"):
        text = (SPACE / name).read_text(encoding="utf-8")
        # No key, and no way to pass one in: the whole point of hosted mode.
        assert not re.search(r"(OPENAI|ANTHROPIC|OPENROUTER)_API_KEY\s*[:=]\s*\S", text), name
