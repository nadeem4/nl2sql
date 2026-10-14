import logging
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, SecretStr

logger = logging.getLogger(__name__)


class AzureSecretConfig(BaseModel):
    """Settings for Azure Key Vault.

    Without all three of ``client_id``, ``client_secret`` and ``tenant_id``
    the provider signs in with ``DefaultAzureCredential`` (managed identity,
    the Azure CLI, and so on).
    """

    model_config = ConfigDict(extra="forbid")

    vault_url: str = Field(..., description="URL of the Key Vault.")
    client_id: Optional[str] = Field(None, description="Service principal client ID.")
    client_secret: Optional[SecretStr] = Field(None, description="Service principal secret.")
    tenant_id: Optional[str] = Field(None, description="Azure tenant ID.")


class AzureSecretProvider:
    """Reads ``${id:secret-name}`` from Azure Key Vault."""

    interface_version = 1
    config_model = AzureSecretConfig

    def __init__(self, client: Any):
        self.client = client

    @classmethod
    def from_config(cls, cfg: AzureSecretConfig) -> "AzureSecretProvider":
        try:
            from azure.identity import ClientSecretCredential, DefaultAzureCredential
            from azure.keyvault.secrets import SecretClient
        except ImportError:
            raise ImportError("Missing dependencies 'azure-identity' and 'azure-keyvault-secrets'. "
                              "Install 'nl2sql-engine[azure]'.") from None

        if cfg.client_id and cfg.client_secret and cfg.tenant_id:
            credential = ClientSecretCredential(
                tenant_id=cfg.tenant_id,
                client_id=cfg.client_id,
                client_secret=cfg.client_secret.get_secret_value(),
            )
        else:
            credential = DefaultAzureCredential()
        return cls(SecretClient(vault_url=cfg.vault_url, credential=credential))

    def get_secret(self, key: str) -> Optional[str]:
        try:
            return self.client.get_secret(key).value
        except Exception as e:
            logger.error(f"Failed to fetch secret '{key}' from Azure Key Vault: {e}")
            return None
