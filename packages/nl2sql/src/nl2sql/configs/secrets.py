
from typing import List

from pydantic import BaseModel, Field, field_validator
from nl2sql.secrets.models import SecretProviderConfig

class SecretsFileConfig(BaseModel):
    """File-level schema for secrets.yaml."""
    version: int = Field(1, description="Schema version")
    providers: List[SecretProviderConfig] = Field(default_factory=list)

    @field_validator("providers", mode="before")
    @classmethod
    def _empty_providers_means_none_configured(cls, value):
        """Treat ``providers:`` with nothing under it as no providers.

        Secret providers are optional, so the default alone is not enough:
        pydantic applies it only when the key is absent, and a user who
        comments out every example leaves the key present with a null value.
        YAML parses that as ``None``, which the annotation rejects. Coerce it
        here so the missing key and the empty key behave the same.
        """
        return [] if value is None else value
