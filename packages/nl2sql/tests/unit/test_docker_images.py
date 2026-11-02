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
    ROOT / "Dockerfile",
    ROOT / "deploy" / "huggingface" / "Dockerfile",
]
PYPROJECTS = [
    ROOT / "packages" / "sdk" / "pyproject.toml",
    ROOT / "packages" / "nl2sql" / "pyproject.toml",
    ROOT / "packages" / "api" / "pyproject.toml",
]
TEST_WORKFLOW = ROOT / ".github" / "workflows" / "test.yml"
PUBLISH_WORKFLOW = ROOT / ".github" / "workflows" / "publish_pypi.yaml"
IMAGE = ROOT / "Dockerfile"
COMPOSE = ROOT / "deploy" / "compose"

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
        if "docker build" in str(job.get("steps", "")) and "-f Dockerfile" in str(job.get("steps", ""))
    ]
    assert builds, "test.yml has no job that runs `docker build -f Dockerfile`"
    # Never a push: publishing is the release's job.
    for name in builds:
        steps = str(jobs[name]["steps"])
        assert "docker push" not in steps
        assert "docker/login-action" not in steps


def test_the_api_image_reads_a_mounted_config_folder():
    """``nl2sql-api`` starts from ``NL2SQL_CONFIG``: a config folder mounted at /etc/nl2sql."""
    dockerfile = IMAGE.read_text(encoding="utf-8")

    assert re.search(r"(?m)^(ENV\s+|\s+)NL2SQL_CONFIG=/etc/nl2sql/nl2sql\.yaml", dockerfile)
    assert "COPY ./packages/sdk" in dockerfile
    for gone in ("DATASOURCE_CONFIG", "LLM_CONFIG", "POLICIES_CONFIG", "NL2SQL_API_", "ENV_FILE_PATH"):
        assert gone not in dockerfile


def _image_text() -> str:
    return IMAGE.read_text(encoding="utf-8")


def test_the_dockerfile_moved_to_the_repository_root():
    assert IMAGE.is_file()
    assert not (ROOT / "packages" / "api" / "Dockerfile").exists()


def test_api_image_builds_the_ui_in_a_node_stage():
    text = _image_text()

    assert re.search(r"(?mi)^FROM\s+node:\S+\s+AS\s+ui\b", text)
    assert "npm ci" in text and "npm run build" in text
    assert re.search(r"(?m)^COPY --from=ui\b.*nl2sql_api/web/static", text)


def test_api_image_installs_the_five_adapters_and_the_sql_server_driver():
    text = _image_text()

    for needle in ("msodbcsql18", "unixodbc", "ACCEPT_EULA=Y", "postgres", "mysql", "mssql", "duckdb"):
        assert needle in text
    # CL2 adds the CLI (D230); the image does not install it yet.
    assert "nl2sql-cli" not in text


def test_api_image_runs_one_worker():
    cmd = re.search(r"(?m)^CMD\s+(\[.*\])\s*$", _image_text())

    assert cmd, "the image has no exec-form CMD"
    # serve.main, never bare uvicorn on main:app (it skips the mode checks and the .env load) and never --workers.
    assert '"nl2sql-api", "serve"' in cmd.group(1)
    assert "uvicorn" not in cmd.group(1) and "--workers" not in cmd.group(1)


def test_api_image_runs_as_a_non_root_user():
    assert re.search(r"(?m)^USER\s+(?!root)\S+", _image_text())


def test_api_image_healthcheck_is_ready():
    healthcheck = re.search(r"(?s)^HEALTHCHECK.*?(?:\n\n|\Z)", _image_text().replace("\r\n", "\n"), re.M)

    assert healthcheck and "/api/v1/ready" in healthcheck.group(0)


def test_compose_file_shape():
    compose = yaml.safe_load((COMPOSE / "docker-compose.yml").read_text(encoding="utf-8"))

    assert list(compose["services"]) == ["api"]
    api = compose["services"]["api"]
    assert api["build"]["dockerfile"] == "Dockerfile" and api["build"]["context"] == "../.."
    assert any(v.startswith("nl2sql-data:/data") for v in api["volumes"])
    assert any(v.startswith("./config:/etc/nl2sql") for v in api["volumes"])
    assert list(compose["volumes"]) == ["nl2sql-data"]
    assert "/api/v1/ready" in str(api["healthcheck"]["test"])
    assert "env_file" in api


def test_compose_example_config_uses_api_keys(monkeypatch):
    from nl2sql import load_config

    for key in ("OPENAI_API_KEY", "NL2SQL_MODE"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("NL2SQL_DATA_DIR", "/data")
    config = load_config(COMPOSE / "config" / "nl2sql.yaml")

    assert config.api.auth == "api_keys"
    assert config.storage.path.name == "data"
    assert [d.id for d in config.datasources] == ["chinook"]
    assert "/opt/nl2sql/samples/data/chinook.sqlite" in str(config.datasources[0].connection)


def test_compose_env_example_lists_the_variables_and_holds_no_key():
    lines = (COMPOSE / ".env.example").read_text(encoding="utf-8").splitlines()
    names = {line.split("=")[0].lstrip("# ") for line in lines if "=" in line}

    assert {"OPENAI_API_KEY", "NL2SQL_DATA_DIR", "NL2SQL_MODE"} <= names
    for line in lines:
        if line.startswith("OPENAI_API_KEY=") or line.startswith("ANTHROPIC_API_KEY="):
            assert line.split("=", 1)[1] == ""


def test_api_wheel_carries_the_built_ui():
    """The wheel ships what `npm run build` wrote, and both workflows that build wheels run it first."""
    with (ROOT / "packages" / "api" / "pyproject.toml").open("rb") as handle:
        data = tomllib.load(handle)["tool"]["setuptools"]["package-data"]["nl2sql_api.web"]
    assert "static/*" in data and "static/assets/*" in data

    for workflow in (TEST_WORKFLOW, PUBLISH_WORKFLOW):
        steps = yaml.safe_load(workflow.read_text(encoding="utf-8"))["jobs"]["build"]["steps"]
        text = [str(step) for step in steps]
        ui = next(i for i, t in enumerate(text) if "npm run build" in t)
        wheels = next(i for i, t in enumerate(text) if "python -m build" in t)
        assert ui < wheels, f"{workflow.name}: the UI must be built before the wheels"
        assert any("static/index.html" in t for t in text), f"{workflow.name}: no check that the wheel has the page"


def test_api_image_job_checks_the_driver_the_size_and_the_page():
    jobs = yaml.safe_load(TEST_WORKFLOW.read_text(encoding="utf-8"))["jobs"]
    steps = str(jobs["api-image"]["steps"])

    assert "pyodbc.drivers()" in steps
    assert "GITHUB_STEP_SUMMARY" in steps
    assert "curl" in steps and "docker run -d" in steps
