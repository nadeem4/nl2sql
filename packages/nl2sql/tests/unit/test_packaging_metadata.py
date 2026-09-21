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
