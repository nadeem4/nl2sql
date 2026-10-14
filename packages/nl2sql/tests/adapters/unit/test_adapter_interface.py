"""Every bundled adapter declares the adapter interface the registry checks.

The registry imports an adapter only when a config names it, then checks
``interface_version`` and looks for each of ``driver_modules`` with
``importlib.util.find_spec`` before building it, reporting ``install_hint``
if one is missing. See docs/architecture/configuration_v2.md, "Interfaces and
versions".
"""
from importlib.metadata import entry_points

import pytest

from nl2sql_sdk import DatasourceAdapterProtocol

# One per name: an editable install next to a checkout can list a name twice.
ENTRY_POINTS = sorted({ep.name: ep for ep in entry_points(group="nl2sql.adapters")}.values(),
                      key=lambda ep: ep.name)

DRIVER_MODULES = {
    "duckdb": ("duckdb", "duckdb_engine"),
    "mssql": ("pyodbc",),
    "mysql": ("pymysql",),
    "postgres": ("psycopg2",),
    "sqlite": ("sqlite3",),
}

BUNDLED = [ep for ep in ENTRY_POINTS if ep.name in DRIVER_MODULES]


def test_every_bundled_adapter_is_checked():
    assert {ep.name for ep in BUNDLED} == set(DRIVER_MODULES)


def test_the_protocol_declares_the_registry_attributes():
    declared = DatasourceAdapterProtocol.__annotations__
    assert {"interface_version", "driver_modules", "install_hint"} <= set(declared)


@pytest.mark.parametrize("entry_point", BUNDLED, ids=lambda ep: ep.name)
def test_bundled_adapter_declares_interface_version_1(entry_point):
    assert entry_point.load().interface_version == 1


@pytest.mark.parametrize("entry_point", BUNDLED, ids=lambda ep: ep.name)
def test_bundled_adapter_declares_its_driver_modules(entry_point):
    assert entry_point.load().driver_modules == DRIVER_MODULES[entry_point.name]


@pytest.mark.parametrize("entry_point", [ep for ep in BUNDLED if ep.name != "sqlite"], ids=lambda ep: ep.name)
def test_bundled_adapter_install_hint_names_its_extra(entry_point):
    assert f"nl2sql-engine[{entry_point.name}]" in entry_point.load().install_hint


def test_sqlite_install_hint_says_it_ships_with_python():
    sqlite = next(ep for ep in BUNDLED if ep.name == "sqlite").load()
    assert "Python" in sqlite.install_hint
