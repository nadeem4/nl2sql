import logging
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, SecretStr

logger = logging.getLogger(__name__)


class HashiCorpSecretConfig(BaseModel):
    """Settings for HashiCorp Vault (KV version 2, token auth)."""

    model_config = ConfigDict(extra="forbid")

    url: str = Field(..., description="URL of the Vault server.")
    token: SecretStr = Field(..., description="Vault token.")
    mount_point: str = Field("secret", description="Mount point of the KV v2 secrets engine.")


class HashiCorpSecretProvider:
    """Reads ``${id:path:field}`` from HashiCorp Vault's KV v2 engine.

    The key is the secret's path and, after a colon, the field to read; a key
    without a colon reads the field ``value``.
    """

    interface_version = 1
    config_model = HashiCorpSecretConfig

    def __init__(self, client: Any, mount_point: str = "secret"):
        self.client = client
        self.mount_point = mount_point

    @classmethod
    def from_config(cls, cfg: HashiCorpSecretConfig) -> "HashiCorpSecretProvider":
        try:
            import hvac
        except ImportError:
            raise ImportError("Missing dependency 'hvac'. Install 'nl2sql-engine[hashicorp]'.") from None
        client = hvac.Client(url=cfg.url, token=cfg.token.get_secret_value())
        return cls(client, cfg.mount_point)

    def get_secret(self, key: str) -> Optional[str]:
        path, _, field = key.partition(":")
        try:
            response = self.client.secrets.kv.v2.read_secret_version(path=path, mount_point=self.mount_point)
        except Exception as e:
            logger.error(f"Failed to fetch secret '{key}' from Vault: {e}")
            return None
        return response["data"]["data"].get(field or "value")
