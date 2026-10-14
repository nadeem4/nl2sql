import os
from typing import Optional

from pydantic import BaseModel, ConfigDict


class EnvSecretConfig(BaseModel):
    """The ``env`` provider takes no settings."""

    model_config = ConfigDict(extra="forbid")


class EnvSecretProvider:
    """Reads ``${env:NAME}`` from the environment variable ``NAME``."""

    interface_version = 1
    config_model = EnvSecretConfig

    @classmethod
    def from_config(cls, cfg: EnvSecretConfig) -> "EnvSecretProvider":
        return cls()

    def get_secret(self, key: str) -> Optional[str]:
        return os.environ.get(key)
