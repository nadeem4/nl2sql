"""An empty ``secrets.yaml`` means "no secret providers", not a broken config.

Secret providers are optional: the shipped ``configs/secrets.yaml`` ships with
every example commented out, which YAML parses as ``providers: None``. That is
the default non-demo path, so it has to load. ``NL2SQLContext.__init__`` calls
``load_secrets``, and a hard failure there breaks a fresh install outright.
"""

from __future__ import annotations

import pathlib

import pytest

from nl2sql.configs import ConfigManager

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
SHIPPED_SECRETS = REPO_ROOT / "configs" / "secrets.yaml"

ALL_COMMENTED = """
version: 1
providers:
  # Example: Azure Key Vault
  # - id: azure-prod
  #   type: azure
  #   vault_url: "https://my-vault.vault.azure.net/"
"""

EMPTY_FILE = ""

NO_PROVIDERS_KEY = "version: 1\n"

EXPLICIT_EMPTY_LIST = "version: 1\nproviders: []\n"


@pytest.mark.parametrize(
    "content",
    [
        pytest.param(ALL_COMMENTED, id="all-commented"),
        pytest.param(EMPTY_FILE, id="empty-file"),
        pytest.param(NO_PROVIDERS_KEY, id="no-providers-key"),
        pytest.param(EXPLICIT_EMPTY_LIST, id="explicit-empty-list"),
    ],
)
def test_empty_secrets_file_loads_as_no_providers(tmp_path, content):
    path = tmp_path / "secrets.yaml"
    path.write_text(content, encoding="utf-8")

    assert ConfigManager(tmp_path).load_secrets(path) == []


def test_shipped_secrets_config_loads():
    """The regression that shipped: the repo's own default file must load."""
    assert SHIPPED_SECRETS.exists(), f"missing shipped config: {SHIPPED_SECRETS}"

    assert ConfigManager(REPO_ROOT).load_secrets(SHIPPED_SECRETS) == []


def test_configured_providers_still_parse(tmp_path):
    path = tmp_path / "secrets.yaml"
    path.write_text(
        "version: 1\n"
        "providers:\n"
        "  - id: azure-prod\n"
        "    type: azure\n"
        '    vault_url: "https://my-vault.vault.azure.net/"\n'
        "  - id: aws-main\n"
        "    type: aws\n"
        '    region_name: "us-east-1"\n',
        encoding="utf-8",
    )

    providers = ConfigManager(tmp_path).load_secrets(path)

    assert [(p.id, p.type) for p in providers] == [
        ("azure-prod", "azure"),
        ("aws-main", "aws"),
    ]
    assert providers[0].vault_url == "https://my-vault.vault.azure.net/"
