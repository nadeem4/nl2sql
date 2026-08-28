from __future__ import annotations

import pathlib

import pytest
import yaml

from nl2sql.cli.demo.writers.docker import DockerWriter

DB_SERVICES = ("manufacturing_ref", "manufacturing_ops", "manufacturing_supply")

SECRETS = {
    "DEMO_REF_PASSWORD": "ref-pw",
    "DEMO_OPS_PASSWORD": "ops-pw",
    "DEMO_POSTGRES_PASSWORD": "pg-pw",
    "DEMO_SUPPLY_PASSWORD": "supply-pw",
    "DEMO_MYSQL_ROOT_PASSWORD": "mysql-pw",
    "DEMO_HISTORY_PASSWORD": "history-pw",
    "DEMO_MSSQL_SA_PASSWORD": "sa-pw",
}


@pytest.fixture
def compose(tmp_path: pathlib.Path) -> dict:
    """Generate the demo stack and return the parsed compose file.

    Empty row lists keep the fixture fast: the compose template is written
    verbatim, so the generated SQL contents do not matter here.
    """
    DockerWriter.write_docker(
        tmp_path,
        SECRETS,
        ([], [], [], [], [], []),
        ([], [], []),
        ([], [], [], []),
        ([], [], []),
    )
    text = (tmp_path / "docker-compose.demo.yml").read_text(encoding="utf-8")
    return yaml.safe_load(text)


def test_app_service_exists_and_exposes_port_8000(compose):
    services = compose["services"]
    assert "app" in services, sorted(services)
    assert "8000:8000" in services["app"]["ports"]


def test_app_depends_on_database_services(compose):
    depends_on = compose["services"]["app"]["depends_on"]
    for name in DB_SERVICES:
        assert name in depends_on, depends_on


def test_mssql_service_is_profile_gated(compose):
    assert compose["services"]["manufacturing_history"]["profiles"] == ["mssql"]


def test_nothing_outside_the_mssql_profile_depends_on_mssql(compose):
    for name, service in compose["services"].items():
        if service.get("profiles") == ["mssql"]:
            continue
        assert "manufacturing_history" not in (service.get("depends_on") or {}), name


def test_app_waits_for_healthy_databases(compose):
    depends_on = compose["services"]["app"]["depends_on"]
    for name in DB_SERVICES:
        assert depends_on[name]["condition"] == "service_healthy"


def test_app_reuses_the_demo_env_file(compose):
    assert compose["services"]["app"]["env_file"] == ["../.env.demo"]
