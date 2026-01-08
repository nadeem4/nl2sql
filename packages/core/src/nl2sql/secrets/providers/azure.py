from typing import Optional
import os
import logging

logger = logging.getLogger(__name__)

class AzureSecretProvider:
    """Fetches secrets from Azure Key Vault."""
    
    def __init__(self, vault_url: Optional[str] = None, client_id: Optional[str] = None, client_secret: Optional[str] = None, tenant_id: Optional[str] = None):
        try:
            from azure.identity import DefaultAzureCredential, ClientSecretCredential
            from azure.keyvault.secrets import SecretClient
            
            self.vault_url = vault_url or os.environ.get("AZURE_KEYVAULT_URL")
            if not self.vault_url:
                raise ValueError("Vault URL is required. specific via config or 'AZURE_KEYVAULT_URL'.")
                
            if client_id and client_secret and tenant_id:
                credential = ClientSecretCredential(
                    tenant_id=tenant_id,
                    client_id=client_id,
                    client_secret=client_secret
                )
            else:
                credential = DefaultAzureCredential()

            self.client = SecretClient(vault_url=self.vault_url, credential=credential)
            self._available = True
        except ImportError:
            self.client = None
            self._available = False
            logger.warning("Azure Secret Provider initialized but dependencies missing. Install 'nl2sql-core[azure]'.")
        except Exception as e:
            self.client = None
            self._available = False
            logger.warning(f"Azure Secret Provider initialization failed: {e}")

    def get_secret(self, key: str) -> Optional[str]:
        if not self._available or not self.client:
             raise ImportError("Azure Secret Provider is not available.")
             
        try:
            # key is the Secret Name
            secret = self.client.get_secret(key)
            return secret.value
        except Exception as e:
            logger.error(f"Failed to fetch secret '{key}' from Azure Key Vault: {e}")
            return None
