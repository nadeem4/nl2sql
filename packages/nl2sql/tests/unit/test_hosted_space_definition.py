"""The Hugging Face Space definition says what a Docker Space needs, and agrees
with the Dockerfile beside it.

Nobody can deploy the Space from here, so what is checkable is the drift: a
front-matter field that goes missing, or an `app_port` that stops matching the
port the container listens on, both fail the Space only after a push.
"""
import os
import pathlib
import re
import shutil
import subprocess

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


def test_what_the_engine_is_installed_from_is_one_rewritable_line():
    """The publish workflow pins the image by rewriting `ARG NL2SQL_SPEC=`.

    A release rewrites it to `nl2sql-engine[demo]==X.Y.Z`. It is declared
    before the install, so rewriting it both pins the engine and busts the
    cache for every layer below. Nothing else feeds it: the old `NL2SQL_REF`
    is gone, so no deploy can pin only half of it.
    """
    lines = (SPACE / "Dockerfile").read_text(encoding="utf-8").splitlines()

    specs = [i for i, line in enumerate(lines) if line.startswith("ARG NL2SQL_SPEC=")]
    install = next(i for i, line in enumerate(lines) if "pip install" in line and "NL2SQL_SPEC" in line)

    assert len(specs) == 1 and specs[0] < install
    assert not any(line.startswith("ARG NL2SQL_REF=") for line in lines)
    assert "${" not in lines[specs[0]]


def test_the_healthcheck_polls_the_health_route():
    dockerfile = (SPACE / "Dockerfile").read_text(encoding="utf-8")
    healthcheck = dockerfile[dockerfile.index("HEALTHCHECK"):]

    assert "/api/health" in healthcheck.split("\n\n")[0]


def test_compose_builds_with_the_same_single_argument():
    compose = yaml.safe_load((SPACE / "docker-compose.yml").read_text(encoding="utf-8"))
    args = compose["services"]["playground"]["build"].get("args") or {}

    assert "NL2SQL_REF" not in args


def test_the_source_sha_file_records_what_the_space_was_built_from():
    sha = (SPACE / "SOURCE_SHA").read_text(encoding="utf-8").strip()

    # In the repository it is the branch name; the publish workflow overwrites
    # it with the commit it deployed from in the copy it pushes to the Space.
    assert sha
    assert "\n" not in sha


def test_the_space_holds_no_api_key():
    for name in ("README.md", "Dockerfile", "docker-compose.yml"):
        text = (SPACE / name).read_text(encoding="utf-8")
        # No key, and no way to pass one in: the whole point of hosted mode.
        assert not re.search(r"(OPENAI|ANTHROPIC|OPENROUTER)_API_KEY\s*[:=]\s*\S", text), name


# The release that published v0.2.0 pushed the Space minutes after PyPI took
# the upload, and the Hugging Face builder's `pip install
# "nl2sql-engine[demo]==0.2.0"` still saw only 0.1.x -- although a fresh
# install from PyPI had already worked on a GitHub runner. The index a builder
# sees can lag; the install retries, bounded, rather than failing the build.
INSTALL_ATTEMPTS = 10
INSTALL_DELAY_SEC = 30


def _install_instruction() -> str:
    """The shell of the `RUN` that installs NL2SQL_SPEC, continuations joined."""
    lines = (SPACE / "Dockerfile").read_text(encoding="utf-8").splitlines()
    for start, line in enumerate(lines):
        if not line.startswith("RUN "):
            continue
        end = start
        while lines[end].rstrip().endswith("\\"):
            end += 1
        instruction = "\n".join(lines[start:end + 1])
        if "NL2SQL_SPEC" in instruction:
            return instruction[len("RUN "):].replace("\\\n", " ")
    raise AssertionError("no RUN instruction installs ${NL2SQL_SPEC}")


def test_the_install_retries_a_bounded_number_of_times_without_pip_cache():
    shell = _install_instruction()

    assert '--no-cache-dir "${NL2SQL_SPEC}"' in shell
    assert f"-ge {INSTALL_ATTEMPTS}" in shell
    assert f"sleep {INSTALL_DELAY_SEC}" in shell


def _run_install(tmp_path: pathlib.Path, failures: int) -> tuple[int, int, list[str]]:
    """Runs the install shell against a fake pip that fails `failures` times.

    Returns the exit code, how many times the engine install ran, and every
    `sleep` argument.
    """
    sh = shutil.which("sh")
    if sh is None:
        pytest.skip("needs a POSIX sh")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "pip").write_text(
        "#!/bin/sh\n"
        'case "$*" in *--upgrade*) exit 0 ;; esac\n'
        'n=$(( $(cat "$STATE/count" 2>/dev/null || echo 0) + 1 ))\n'
        'echo "$n" > "$STATE/count"\n'
        '[ "$n" -gt "$FAILURES" ]\n',
        encoding="utf-8", newline="\n",
    )
    (bin_dir / "sleep").write_text(
        '#!/bin/sh\necho "$1" >> "$STATE/sleeps"\n', encoding="utf-8", newline="\n",
    )
    for stub in bin_dir.iterdir():
        stub.chmod(0o755)

    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "STATE": str(tmp_path),
        "FAILURES": str(failures),
        "NL2SQL_SPEC": "nl2sql-engine[demo]==9.9.9",
    }
    result = subprocess.run([sh, "-c", _install_instruction()], env=env, capture_output=True, text=True)
    count = int((tmp_path / "count").read_text().strip())
    sleeps_file = tmp_path / "sleeps"
    sleeps = sleeps_file.read_text().split() if sleeps_file.exists() else []
    return result.returncode, count, sleeps


def test_an_install_that_succeeds_at_once_never_waits(tmp_path):
    assert _run_install(tmp_path, failures=0) == (0, 1, [])


def test_an_index_that_catches_up_is_retried_until_the_install_works(tmp_path):
    code, count, sleeps = _run_install(tmp_path, failures=3)

    assert (code, count) == (0, 4)
    assert sleeps == [str(INSTALL_DELAY_SEC)] * 3


def test_an_install_that_never_works_fails_the_build_after_the_last_attempt(tmp_path):
    code, count, sleeps = _run_install(tmp_path, failures=INSTALL_ATTEMPTS + 5)

    assert code != 0
    assert count == INSTALL_ATTEMPTS
    assert len(sleeps) == INSTALL_ATTEMPTS - 1
