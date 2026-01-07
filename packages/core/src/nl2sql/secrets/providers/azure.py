from typing import Optional
import os
import logging

logger = logging.getLogger(__name__)

class AzureSecretProvider:
    """Fetches secrets from Azure Key Vault."""
    
    def __init__(self):
        try:
            from azure.identity import DefaultAzureCredential
            from azure.keyvault.secrets import SecretClient
            
            vault_url = os.environ.get("AZURE_KEYVAULT_URL")
            if not vault_url:
                raise ValueError("Environment variable 'AZURE_KEYVAULT_URL' is required for Azure Secret Provider.")
                
            credential = DefaultAzureCredential()
            self.client = SecretClient(vault_url=vault_url, credential=credential)
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
