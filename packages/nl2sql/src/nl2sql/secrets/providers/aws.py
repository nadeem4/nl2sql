import logging
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


class AwsSecretConfig(BaseModel):
    """Settings for AWS Secrets Manager.

    Both are optional: boto3's own lookup (its environment variables, shared
    config and instance role) supplies what is left out.
    """

    model_config = ConfigDict(extra="forbid")

    region_name: Optional[str] = Field(None, description="AWS region, e.g. us-east-1.")
    profile_name: Optional[str] = Field(None, description="AWS profile from the shared config.")


class AwsSecretProvider:
    """Reads ``${id:secret-id}`` from AWS Secrets Manager."""

    interface_version = 1
    config_model = AwsSecretConfig

    def __init__(self, client: Any):
        self.client = client

    @classmethod
    def from_config(cls, cfg: AwsSecretConfig) -> "AwsSecretProvider":
        try:
            import boto3
        except ImportError:
            raise ImportError("Missing dependency 'boto3'. Install 'nl2sql-engine[aws]'.") from None
        session = boto3.Session(profile_name=cfg.profile_name, region_name=cfg.region_name)
        return cls(session.client("secretsmanager"))

    def get_secret(self, key: str) -> Optional[str]:
        try:
            response = self.client.get_secret_value(SecretId=key)
        except Exception as e:
            logger.error(f"Failed to fetch secret '{key}' from AWS: {e}")
            return None
        # Binary secrets are not supported: every use is a text value.
        return response.get("SecretString")
