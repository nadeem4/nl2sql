
from typing import Literal, Optional, Union
from pydantic import BaseModel, Field, SecretStr

class BaseSecretConfig(BaseModel):
    """Base configuration for all secret providers."""
    id: str = Field(..., description="Unique identifier for this provider instance. Used as the scheme in secret references (e.g. ${id:key}).")
    type: str

class AwsSecretConfig(BaseSecretConfig):
    """Configuration for AWS Secrets Manager.
    
    Attributes:
        type: Must be 'aws'.
        region_name: AWS Region (e.g. us-east-1). Defaults to env var if None.
        profile_name: AWS Profile. Defaults to standard boto3 lookup if None.
    """
    type: Literal["aws"] = "aws"
    region_name: Optional[str] = Field(None, description="AWS Region. If None, uses AWS_DEFAULT_REGION env var.")
    profile_name: Optional[str] = Field(None, description="AWS Profile. If None, uses default profile.")

class AzureSecretConfig(BaseSecretConfig):
    """Configuration for Azure Key Vault.
    
    Attributes:
        type: Must be 'azure'.
        vault_url: The URL of the Key Vault.
        client_id: Service Principal ID.
        client_secret: Service Principal Secret.
        tenant_id: Azure Tenant ID.
    """
    type: Literal["azure"] = "azure"
    vault_url: str = Field(..., description="URL of the Key Vault.")
    
    client_id: Optional[str] = Field(None, description="Azure Client ID (Service Principal).")
    client_secret: Optional[str] = Field(None, description="Azure Client Secret.")
    tenant_id: Optional[str] = Field(None, description="Azure Tenant ID.")

class HashiCorpSecretConfig(BaseSecretConfig):
    """Configuration for HashiCorp Vault."""
    type: Literal["hashi"] = "hashi"
    url: str = Field(..., description="URL of the HashiCorp Vault server.")
    token: Optional[str] = Field(None, description="Vault Token.")
    mount_point: str = Field("secret", description="Secrets engine mount point.")

class EnvSecretConfig(BaseSecretConfig):
    """Configuration for Environment Variable Provider (Explicit)."""
    type: Literal["env"] = "env"

# Polymorphic Union
SecretProviderConfig = Union[
    AwsSecretConfig,
    AzureSecretConfig,
    HashiCorpSecretConfig,
    EnvSecretConfig
]

from typing import List
class SecretsFileConfig(BaseModel):
    """File-level schema for secrets.yaml."""
    version: int = Field(1, description="Schema version")
    providers: List[SecretProviderConfig]

