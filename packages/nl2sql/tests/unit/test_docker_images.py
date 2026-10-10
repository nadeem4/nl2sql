"""Every Docker image this repository builds runs a Python every package supports,
and a pull request builds the API image before a release has to.

v0.2.0 published to PyPI and then failed to publish `ghcr.io/nadeem4/nl2sql-api`:
packages/api/Dockerfile was still `FROM python:3.11-slim` while all three
packages require `>=3.12`, and nothing before the release ever built it.
"""
import pathlib
import re
import tomllib

import pytest
from packaging.specifiers import SpecifierSet
from packaging.version import Version

yaml = pytest.importorskip("yaml")

ROOT = pathlib.Path(__file__).resolve().parents[4]
DOCKERFILES = [
    ROOT / "packages" / "api" / "Dockerfile",
    ROOT / "deploy" / "huggingface" / "Dockerfile",
]
PYPROJECTS = [
    ROOT / "packages" / "adapter-sdk" / "pyproject.toml",
    ROOT / "packages" / "nl2sql" / "pyproject.toml",
    ROOT / "packages" / "api" / "pyproject.toml",
]
TEST_WORKFLOW = ROOT / ".github" / "workflows" / "test.yml"

_FROM_PYTHON = re.compile(r"(?mi)^FROM\s+(?:docker\.io/)?(?:library/)?python:(\d+\.\d+)")


def python_base(dockerfile: pathlib.Path) -> Version:
    """The X.Y of the image's `FROM python:X.Y...` line."""
    found = _FROM_PYTHON.findall(dockerfile.read_text(encoding="utf-8"))
    assert found, f"{dockerfile.relative_to(ROOT)} has no `FROM python:X.Y` line"
    assert len(set(found)) == 1, f"{dockerfile.relative_to(ROOT)} builds on more than one Python: {found}"
    return Version(found[0])


def requires_python(pyproject: pathlib.Path) -> SpecifierSet:
    with pyproject.open("rb") as handle:
        return SpecifierSet(tomllib.load(handle)["project"]["requires-python"])


def test_python_base_reads_the_minor_version_of_the_from_line(tmp_path):
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("# FROM python:2.7\nFROM python:3.12-slim\nRUN true\n", encoding="utf-8")

    assert python_base(dockerfile) == Version("3.12")


@pytest.mark.parametrize("dockerfile", DOCKERFILES, ids=lambda p: str(p.relative_to(ROOT)))
@pytest.mark.parametrize("pyproject", PYPROJECTS, ids=lambda p: p.parent.name)
def test_every_image_runs_a_python_every_package_supports(dockerfile, pyproject):
    base = python_base(dockerfile)
    spec = requires_python(pyproject)

    assert spec.contains(base), (
        f"{dockerfile.relative_to(ROOT)} builds on Python {base}, but "
        f"{pyproject.relative_to(ROOT)} requires-python {spec}: pip refuses to install it"
    )


def test_a_pull_request_builds_the_api_image_the_release_publishes():
    """The `ghcr` release job is the only other place this image is built; a
    broken image has to fail a pull request, not a release."""
    jobs = yaml.safe_load(TEST_WORKFLOW.read_text(encoding="utf-8"))["jobs"]

    builds = [
        name for name, job in jobs.items()
        if "docker build" in str(job.get("steps", "")) and "packages/api/Dockerfile" in str(job.get("steps", ""))
    ]
    assert builds, "test.yml has no job that runs `docker build -f packages/api/Dockerfile`"
    # Never a push: publishing is the release's job.
    for name in builds:
        steps = str(jobs[name]["steps"])
        assert "docker push" not in steps
        assert "docker/login-action" not in steps
