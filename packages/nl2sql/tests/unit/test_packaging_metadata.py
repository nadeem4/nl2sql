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


def _project(package: str) -> dict:
    return tomllib.loads((ROOT / "packages" / package / "pyproject.toml").read_text(encoding="utf-8"))["project"]


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
    # typer dropped the [all] extra; installing it only warns.
    assert "typer" in names and not any(d.startswith("typer[") for d in deps)
    # cli/commands/demo.py and cli/demo/manager.py import dotenv.
    assert "python-dotenv" in names


PACKAGES = ["nl2sql", "api", "sdk"]
PROJECT_URLS = {"Homepage", "Documentation", "Source", "Issues", "Demo"}


@pytest.mark.parametrize("package", PACKAGES)
def test_every_distribution_has_a_readme_pypi_can_render(package):
    readme = _project(package).get("readme")
    assert readme, f"{package} declares no readme, so PyPI shows an empty page"
    assert (ROOT / "packages" / package / readme).is_file()


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
