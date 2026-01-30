
from typing import List

from pydantic import BaseModel, Field
from nl2sql.secrets.models import SecretProviderConfig

class SecretsFileConfig(BaseModel):
    """File-level schema for secrets.yaml."""
    version: int = Field(1, description="Schema version")
    providers: List[SecretProviderConfig]

