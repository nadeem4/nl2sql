"""The registry: the lookup from a name in the config to the code behind it.

``Registry.default()`` lists the installed entry points and imports none of
them; a name is imported, and checked, when it is loaded. See
docs/architecture/configuration_v2.md, sections 5 and 6.
"""
import sys
import tomllib
import types
from pathlib import Path

import pytest

import nl2sql.registry as registry_module
from nl2sql.registry import Registry, RegistryError

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


def _installed(monkeypatch, **groups):
    """Pretend these entry points are installed: ``group=[(name, "module:attr", package)]``."""
    scan = {group: () for group in registry_module.GROUPS.values()}
    scan.update({registry_module.GROUPS[kind]: tuple(entries) for kind, entries in groups.items()})
    monkeypatch.setattr(registry_module, "_scan", lambda: scan)


def _plugin(monkeypatch, name, **attrs):
    """An importable module ``name`` holding ``attrs``."""
    module = types.ModuleType(name)
    module.__dict__.update(attrs)
    monkeypatch.setitem(sys.modules, name, module)
    return module


def _adapter(version=1, drivers=(), hint=""):
    return type("Adapter", (), {"interface_version": version, "driver_modules": drivers,
                                "install_hint": hint})


# ----------------------------------------------------------- the six in the plan

def test_default_registry_imports_no_plugin(monkeypatch):
    _installed(monkeypatch, adapter=[("snowflake", "nl2sql_never_imported:Adapter", "nl2sql-snowflake")])

    registry = Registry.default()

    assert registry.names("adapter") == ["snowflake"]
    assert "nl2sql_never_imported" not in sys.modules


def test_two_registries_do_not_share_registrations():
    first, second = Registry.empty(), Registry.empty()

    first.add_adapter("stub", _adapter())

    assert first.names("adapter") == ["stub"]
    assert second.names("adapter") == []
    assert "stub" not in Registry.default().names("adapter")


def test_wrong_interface_version_is_rejected_with_package_name(monkeypatch):
    _plugin(monkeypatch, "nl2sql_snowflake_fake", Adapter=_adapter(version=0))
    _installed(monkeypatch, adapter=[("snowflake", "nl2sql_snowflake_fake:Adapter", "nl2sql-snowflake")])

    with pytest.raises(RegistryError) as excinfo:
        Registry.default().load("adapter", "snowflake")

    assert str(excinfo.value) == "nl2sql-snowflake implements adapter interface 0; this engine needs 1."


def test_missing_driver_reports_install_hint():
    registry = Registry.empty()
    registry.add_adapter("snowflake", _adapter(drivers=("nl2sql_no_such_driver",),
                                               hint='pip install "nl2sql-snowflake[driver]"'))

    with pytest.raises(RegistryError) as excinfo:
        registry.load("adapter", "snowflake")

    message = str(excinfo.value)
    assert "nl2sql_no_such_driver" in message
    assert 'pip install "nl2sql-snowflake[driver]"' in message


def test_duplicate_entry_point_names_both_packages(monkeypatch):
    _installed(monkeypatch, adapter=[("snowflake", "pkg_a:Adapter", "nl2sql-snowflake"),
                                     ("snowflake", "pkg_b:Adapter", "acme-snowflake")])
    registry = Registry.default()

    assert registry.names("adapter") == ["snowflake"]
    with pytest.raises(RegistryError) as excinfo:
        registry.load("adapter", "snowflake")

    message = str(excinfo.value)
    assert "nl2sql-snowflake" in message and "acme-snowflake" in message


def test_decorator_registers_on_that_registry_only():
    mine, other = Registry.empty(), Registry.empty()

    @mine.adapter("stub")
    class Stub(_adapter()):
        pass

    assert mine.load("adapter", "stub") is Stub
    assert other.names("adapter") == []


# ----------------------------------------------------------- the other rules

def test_the_same_entry_point_listed_twice_is_not_a_clash(monkeypatch):
    # An editable install next to a checkout can list one entry point twice.
    adapter = _plugin(monkeypatch, "nl2sql_twice_fake", Adapter=_adapter()).Adapter
    entry = ("twice", "nl2sql_twice_fake:Adapter", "nl2sql-engine")
    _installed(monkeypatch, adapter=[entry, entry])

    assert Registry.default().load("adapter", "twice") is adapter


def test_unknown_name_suggests_the_closest():
    registry = Registry.empty()
    registry.add_adapter("postgres", _adapter())

    with pytest.raises(RegistryError, match="Unknown adapter 'postgress'. Did you mean 'postgres'?"):
        registry.load("adapter", "postgress")


def test_adding_a_name_twice_raises_unless_replace():
    registry = Registry.empty()
    first, second = _adapter(), _adapter()
    registry.add_adapter("stub", first)

    with pytest.raises(RegistryError, match="already"):
        registry.add_adapter("stub", second)
    registry.add_adapter("stub", second, replace=True)

    assert registry.load("adapter", "stub") is second


def test_a_name_in_code_replaces_an_installed_one_only_when_asked(monkeypatch):
    _installed(monkeypatch, adapter=[("postgres", "nl2sql_never_imported:Adapter", "nl2sql-engine")])
    registry = Registry.default()
    mine = _adapter()

    with pytest.raises(RegistryError, match="already"):
        registry.add_adapter("postgres", mine)
    registry.add_adapter("postgres", mine, replace=True)

    assert registry.load("adapter", "postgres") is mine


@pytest.mark.parametrize("kind", ["llm", "embeddings", "secrets"])
def test_every_kind_has_add_and_a_decorator(kind):
    registry = Registry.empty()
    thing = types.SimpleNamespace(interface_version=registry_module.INTERFACE_VERSIONS[kind], requires=(),
                                  install_hint="")

    getattr(registry, f"add_{kind}")("a", thing)
    getattr(registry, kind)("b")(thing)

    assert registry.names(kind) == ["a", "b"]
    assert registry.load(kind, "a") is thing


def test_a_missing_llm_requirement_reports_install_hint():
    registry = Registry.empty()
    registry.add_llm("gemini", types.SimpleNamespace(interface_version=1, requires=("nl2sql_no_such_sdk",),
                                                     install_hint='pip install "nl2sql-gemini"'))

    with pytest.raises(RegistryError, match='pip install "nl2sql-gemini"'):
        registry.load("llm", "gemini")


# ----------------------------------------------------------- the built-ins

def _declared(group):
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["entry-points"][group]


def test_the_built_ins_are_declared_in_pyproject():
    from nl2sql.llm.registry import PROVIDER_PRESETS

    assert set(_declared("nl2sql.adapters")) == {"duckdb", "mssql", "mysql", "postgres", "sqlite"}
    assert set(_declared("nl2sql.llms")) == set(PROVIDER_PRESETS)
    assert set(_declared("nl2sql.embeddings")) == {"local", "openai", "openai_compatible"}
    assert set(_declared("nl2sql.secrets")) == {"env", "aws", "azure", "hashicorp"}


@pytest.mark.parametrize("kind", ["adapter", "llm", "embeddings", "secrets"])
def test_the_default_registry_lists_every_built_in(kind):
    group = registry_module.GROUPS[kind]
    assert set(_declared(group)) <= set(Registry.default().names(kind))


@pytest.mark.parametrize("name", ["openai", "anthropic", "openrouter", "ollama"])
def test_every_built_in_llm_provider_loads(name):
    provider = Registry.default().load("llm", name)
    assert provider.interface_version == 1


@pytest.mark.parametrize("name", ["local", "openai", "openai_compatible"])
def test_every_built_in_embeddings_provider_loads(name):
    provider = Registry.default().load("embeddings", name)
    assert provider.interface_version == 2
    assert provider.describe


@pytest.mark.parametrize("name", ["env", "aws", "azure", "hashicorp"])
def test_every_built_in_secret_provider_loads(name):
    assert Registry.default().load("secrets", name).interface_version == 1


def test_list_adapters_shows_a_missing_driver_with_its_hint(monkeypatch):
    from typer.testing import CliRunner

    from nl2sql.cli.main import app

    _plugin(monkeypatch, "nl2sql_snowflake_fake", Adapter=_adapter(
        drivers=("nl2sql_no_such_driver",), hint="pip install nl2sql-snowflake"))
    _installed(monkeypatch, adapter=[("snowflake", "nl2sql_snowflake_fake:Adapter", "nl2sql-snowflake")])

    result = CliRunner().invoke(app, ["list-adapters"], env={"COLUMNS": "300"})

    assert result.exit_code == 0, result.output
    assert "snowflake" in result.output
    assert "pip install nl2sql-snowflake" in result.output
