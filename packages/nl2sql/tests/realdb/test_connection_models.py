"""Each server connects through its adapter's public, strict connection model (adapter SDK v2).

docs/architecture/phase2.md, section 7.
"""
from __future__ import annotations

import pytest

from nl2sql.config import Datasource
from nl2sql.datasources.registry import DatasourceRegistry
from nl2sql.registry import Registry
from nl2sql.secrets.manager import SecretManager


def _registry() -> DatasourceRegistry:
    return DatasourceRegistry(SecretManager(), Registry.default())


def test_each_server_connects_through_its_model(server):
    adapter_cls = Registry.default().load("adapter", server.name)
    connection = adapter_cls.connection_model(**server.connection())

    adapter = _registry().register_datasource(Datasource(id="sample", type=server.name, connection=connection))
    try:
        check = adapter.test_connection()
    finally:
        adapter.close()

    assert adapter.connection is connection
    assert check.ok, check.message
    assert check.server_version


def test_a_misspelt_key_fails_before_connecting(server):
    connection = server.connection()
    connection["hostname"] = connection.pop("host")

    with pytest.raises(ValueError, match="Did you mean 'host'"):
        _registry().register_datasource(Datasource(id="sample", type=server.name, connection=connection))


def test_a_wrong_password_is_a_failed_check_not_an_exception(server):
    connection = {**server.connection(), "password": "not-the-password"}
    adapter = _registry().register_datasource(Datasource(id="sample", type=server.name, connection=connection))
    try:
        check = adapter.test_connection()
    finally:
        adapter.close()

    assert not check.ok
    assert check.message
    assert "not-the-password" not in check.message
