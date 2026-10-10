"""What the published distributions declare: license, notices and dependencies.

The ``nl2sql-engine`` wheel vendors the Chinook database (MIT) and two OFL
fonts, so it has to carry the project's LICENSE and the third-party notices.
setuptools only packs license files from inside the project directory, so the
package keeps copies of the repository-root files; this checks they match.
"""
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
ENGINE = ROOT / "packages" / "nl2sql"
NOTICES = ("LICENSE", "THIRD_PARTY_NOTICES.md")


def _project(package: str) -> dict:
    return tomllib.loads((ROOT / "packages" / package / "pyproject.toml").read_text(encoding="utf-8"))["project"]


@pytest.mark.parametrize("package", ["nl2sql", "api", "adapter-sdk"])
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


PACKAGES = ["nl2sql", "api", "adapter-sdk"]
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
