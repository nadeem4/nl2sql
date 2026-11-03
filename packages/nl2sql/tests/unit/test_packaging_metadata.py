"""What the published distributions declare: license, notices and dependencies.

The ``nl2sql-engine`` wheel vendors the Chinook database (MIT) and two OFL
fonts, so it has to carry the project's LICENSE and the third-party notices.
setuptools only packs license files from inside the project directory, so the
package keeps copies of the repository-root files; this checks they match.
"""
import json
import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[4]
ENGINE = ROOT / "packages" / "nl2sql"
NOTICES = ("LICENSE", "THIRD_PARTY_NOTICES.md")


def _dir(package: str) -> Path:
    return ROOT / "nl2sql-cli" if package == "nl2sql-cli" else ROOT / "packages" / package


def _project(package: str) -> dict:
    return tomllib.loads((_dir(package) / "pyproject.toml").read_text(encoding="utf-8"))["project"]


@pytest.mark.parametrize("package", ["nl2sql", "api", "sdk"])
def test_every_distribution_declares_its_license(package):
    assert _project(package)["license"] == "MIT"


def test_the_engine_wheel_carries_the_license_and_notices():
    assert set(NOTICES) <= set(_project("nl2sql")["license-files"])
    for name in NOTICES:
        assert (ENGINE / name).read_bytes() == (ROOT / name).read_bytes(), f"{name} differs from the root copy"


def test_engine_dependencies_name_what_the_code_imports():
    deps = _project("nl2sql")["dependencies"]
    names = [d.split(">")[0].split("=")[0].split("<")[0].split("~")[0].strip() for d in deps]
    # nl2sql.config.load_env_file imports dotenv.
    assert "python-dotenv" in names


CLI_PROJECT = ROOT / "nl2sql-cli" / "pyproject.toml"


def _cli() -> dict:
    return tomllib.loads(CLI_PROJECT.read_text(encoding="utf-8"))["project"]


def test_nl2sql_command_is_installed_by_nl2sql_cli():
    """The ``nl2sql`` script moved with the CLI; the engine installs no command and has no ``demo`` extra."""
    import importlib

    assert _cli()["name"] == "nl2sql-cli"
    assert _cli()["scripts"] == {"nl2sql": "nl2sql_cli.main:main"}
    module, _, function = _cli()["scripts"]["nl2sql"].partition(":")
    assert callable(getattr(importlib.import_module(module), function))
    engine = _project("nl2sql")
    assert "scripts" not in engine
    assert not {"demo", "tui"} & set(engine["optional-dependencies"])


def test_the_cli_depends_on_the_engine_and_textual_and_the_api_is_its_web_extra():
    """the engine is its one repository dependency, Textual is a plain dependency, the API is the ``web`` extra."""
    cli = _cli()
    engine = Version(_project("nl2sql")["version"])
    requirements = {Requirement(d).name: Requirement(d) for d in cli["dependencies"]}

    assert str(requirements["nl2sql-engine"].specifier) == f"~={engine}"
    assert "textual" in requirements and not requirements["textual"].extras
    assert not {name for name in requirements if name.startswith("nl2sql")} - {"nl2sql-engine"}
    assert set(cli["optional-dependencies"]) == {"web"}
    assert cli["optional-dependencies"]["web"] == [f"nl2sql-api~={engine}"]
    # The release-please marker on each of the CLI's version and pins, so CL2's updater bumps them.
    marked = [line for line in CLI_PROJECT.read_text(encoding="utf-8").splitlines() if "x-release-please-version" in line]
    assert len(marked) == 3 and any("nl2sql-engine" in line for line in marked) and any("nl2sql-api" in line for line in marked)
    assert cli["version"] == str(engine)
    # ...and release-please is told to edit that file, so all four packages release together.
    config = json.loads((ROOT / "release-please-config.json").read_text(encoding="utf-8"))
    extra = [f["path"] for p in config["packages"].values() for f in p.get("extra-files", [])]
    assert "nl2sql-cli/pyproject.toml" in extra
    assert all(f["type"] == "generic" for p in config["packages"].values() for f in p["extra-files"])


def test_the_engine_declares_no_cli_dependencies():
    """rich and typer are the CLI's; the engine imports neither."""
    names = {Requirement(d).name for d in _project("nl2sql")["dependencies"]}
    assert not {"rich", "typer", "inquirerpy"} & names
    assert {"rich", "typer"} <= {Requirement(d).name for d in _cli()["dependencies"]}


PACKAGES = ["nl2sql", "api", "sdk", "nl2sql-cli"]
PROJECT_URLS = {"Homepage", "Documentation", "Source", "Issues", "Demo"}


@pytest.mark.parametrize("package", PACKAGES)
def test_every_distribution_has_a_readme_pypi_can_render(package):
    readme = _project(package).get("readme")
    assert readme, f"{package} declares no readme, so PyPI shows an empty page"
    assert (_dir(package) / readme).is_file()


@pytest.mark.parametrize("package", PACKAGES)
def test_every_distribution_links_home_docs_source_issues_and_demo(package):
    urls = _project(package).get("urls", {})
    assert PROJECT_URLS <= set(urls), f"{package} is missing {PROJECT_URLS - set(urls)}"
    assert all(url.startswith("https://") for url in urls.values())


@pytest.mark.parametrize("package", PACKAGES)
def test_the_demo_link_is_the_custom_domain_a_reader_can_remember(package):
    # The domain redirects its root to the Space; a reader only ever needs the root.
    assert _project(package)["urls"]["Demo"] == "https://nl2sql.codewithnk.com"


@pytest.mark.parametrize("package", PACKAGES)
def test_every_distribution_is_findable_and_classified(package):
    project = _project(package)
    assert project.get("description")
    assert project.get("keywords")
    classifiers = project.get("classifiers", [])
    assert "Programming Language :: Python :: 3.12" in classifiers
    assert any(c.startswith("Development Status ::") for c in classifiers)
    # PEP 639: alongside a `license` expression, a License :: classifier is an error.
    assert not any(c.startswith("License ::") for c in classifiers)


def test_the_api_pins_the_engine_release_it_ships_with():
    """`~=0.1` let the API install any 0.x engine, older ones without the facade methods it calls.

    The three packages release together, so the pin is the engine's own version as a
    compatible release (`~=X.Y.Z`: same minor, this patch or later). The line carries
    the release-please marker and the file is in extra-files, so each release bumps it.
    """
    engine_version = Version(_project("nl2sql")["version"])
    pin = next(d for d in _project("api")["dependencies"] if d.startswith("nl2sql-engine"))
    spec = SpecifierSet(pin.removeprefix("nl2sql-engine"))
    assert str(spec) == f"~={engine_version}", pin
    assert not spec.contains(Version(f"{engine_version.major}.{engine_version.minor + 1}.0"))

    lines = (ROOT / "packages" / "api" / "pyproject.toml").read_text(encoding="utf-8").splitlines()
    assert any("nl2sql-engine" in line and "x-release-please-version" in line for line in lines)

    config = json.loads((ROOT / "release-please-config.json").read_text(encoding="utf-8"))
    extra = [f["path"] for p in config["packages"].values() for f in p.get("extra-files", [])]
    assert "packages/api/pyproject.toml" in extra


def test_the_sdk_is_published_as_nl2sql_sdk():
    sdk = tomllib.loads((ROOT / "packages" / "sdk" / "pyproject.toml").read_text(encoding="utf-8"))
    assert sdk["project"]["name"] == "nl2sql-sdk"
    assert sdk["tool"]["setuptools"]["packages"]["find"]["include"] == ["nl2sql_sdk*"]


def test_the_engine_pins_the_sdk_release_it_ships_with():
    """`nl2sql-adapter-sdk~=0.1` let the engine install any 0.x SDK.

    The three packages release together, so the engine pins the SDK at its own
    version as a compatible release, and release-please bumps the line.
    """
    engine_version = Version(_project("nl2sql")["version"])
    pin = Requirement(next(d for d in _project("nl2sql")["dependencies"] if d.startswith("nl2sql-sdk")))
    assert str(pin.specifier) == f"~={engine_version}", pin
    # The built-in adapters build on nl2sql_sdk.sqlalchemy.
    assert pin.extras == {"sqlalchemy"}, pin
    assert not any(d.startswith("nl2sql-adapter-sdk") for d in _project("nl2sql")["dependencies"])

    lines = (ENGINE / "pyproject.toml").read_text(encoding="utf-8").splitlines()
    assert any("nl2sql-sdk" in line and "x-release-please-version" in line for line in lines)

    config = json.loads((ROOT / "release-please-config.json").read_text(encoding="utf-8"))
    extra = [f["path"] for p in config["packages"].values() for f in p.get("extra-files", [])]
    assert {"packages/sdk/pyproject.toml", "packages/nl2sql/pyproject.toml"} <= set(extra)
    assert "packages/adapter-sdk/pyproject.toml" not in extra


@pytest.mark.parametrize("workflow", ["test.yml", "publish_pypi.yaml"])
def test_the_wheel_smoke_test_installs_the_sdk_wheel(workflow):
    text = (ROOT / ".github" / "workflows" / workflow).read_text(encoding="utf-8")
    assert "dist/nl2sql_sdk-*.whl" in text
    assert "packages/sdk " in text
    assert "adapter-sdk" not in text and "adapter_sdk" not in text


def test_pypi_publishes_the_sdk_from_its_own_environment():
    text = (ROOT / ".github" / "workflows" / "publish_pypi.yaml").read_text(encoding="utf-8")
    assert "package: nl2sql-sdk" in text
    assert "stem: nl2sql_sdk" in text
    assert "environment: pypi-${{ matrix.package }}" in text


@pytest.mark.parametrize("extra", ["mysql", "all"])
def test_the_mysql_extras_install_cryptography_for_mysql_8_logins(extra):
    """MySQL 8's default login, ``caching_sha2_password``, needs ``cryptography`` without TLS.

    ``pymysql[rsa]`` is PyMySQL's own name for that dependency.
    """
    requirements = [Requirement(r) for r in _project("nl2sql")["optional-dependencies"][extra]]
    pymysql = next(r for r in requirements if r.name == "pymysql")
    assert "rsa" in pymysql.extras


def test_pypi_uploads_the_sdk_then_the_engine_then_the_api_then_the_cli_one_at_a_time():
    """The engine needs the SDK, the API the engine, the CLI the engine (and the API for its `web` extra): upload what is depended on first."""
    import re
    from pathlib import Path

    workflow = (Path(__file__).resolve().parents[4] / ".github" / "workflows" / "publish_pypi.yaml").read_text(encoding="utf-8")

    assert "max-parallel: 1" in workflow
    order = re.findall(r"- package: (nl2sql-[a-z]+)", workflow)
    assert order == ["nl2sql-sdk", "nl2sql-engine", "nl2sql-api", "nl2sql-cli"]


def test_the_api_upload_waits_for_the_engine_to_be_on_pypi():
    """The legs are independent (fail-fast is off), so the api leg checks for its engine itself; the engine needs no API."""
    from pathlib import Path

    workflow = (Path(__file__).resolve().parents[4] / ".github" / "workflows" / "publish_pypi.yaml").read_text(encoding="utf-8")
    check = workflow.index("Check the engine is on PyPI")

    assert "matrix.package == 'nl2sql-api'" in workflow[check - 200:check + 400]
    assert "https://pypi.org/pypi/nl2sql-engine/" in workflow[check:check + 1200]
    # PyPI's JSON API can lag a fresh upload: the check retries before it fails the release.
    assert "--retry-all-errors" in workflow[check:check + 1200]
    assert check < workflow.index("Publish ${{ matrix.package }} to PyPI")
    assert "Check the API is on PyPI" not in workflow


def _publish() -> str:
    return (ROOT / ".github" / "workflows" / "publish_pypi.yaml").read_text(encoding="utf-8")


def test_publish_uploads_four_distributions():
    """Build, smoke-install and upload all four, each from its own environment, in the CLI's own directory."""
    workflow = _publish()

    assert "for pkg in packages/sdk packages/nl2sql packages/api nl2sql-cli; do" in workflow
    assert "dist/nl2sql_cli-*.whl" in workflow
    assert "package: nl2sql-cli" in workflow and "stem: nl2sql_cli" in workflow
    # The environment is the contract with PyPI's trusted publisher entry (releasing.md).
    assert "environment: pypi-${{ matrix.package }}" in workflow
    assert "nl2sql-cli[web]" in (ROOT / "scripts" / "fresh_install_check.py").read_text(encoding="utf-8")


def test_the_cli_upload_waits_for_the_engine_and_the_api_to_be_on_pypi():
    """`pip install nl2sql-cli[web]` needs both at the release version."""
    workflow = _publish()
    check = workflow.index("Check the engine and the API are on PyPI")

    assert "matrix.package == 'nl2sql-cli'" in workflow[check - 200:check + 400]
    assert "for package in nl2sql-engine nl2sql-api; do" in workflow[check:check + 1500]
    assert "https://pypi.org/pypi/$package/$version/json" in workflow[check:check + 1500]
    assert check < workflow.index("Publish ${{ matrix.package }} to PyPI")


def test_the_pypi_smoke_installs_the_cli_with_its_web_extra_and_runs_the_command():
    workflow = _publish()

    assert "scripts/fresh_install_check.py --pypi" in workflow
    assert "nl2sql_cli-*.whl" in workflow
    assert "bin/nl2sql --help" in workflow
