from typing import Optional
import os
import logging

logger = logging.getLogger(__name__)

class HashiCorpSecretProvider:
    """Fetches secrets from HashiCorp Vault."""
    
    def __init__(self):
        try:
            import hvac
            url = os.environ.get("VAULT_ADDR", "http://localhost:8200")
            token = os.environ.get("VAULT_TOKEN")
            
            # Simple Token Auth for now, can extend to AppRole later
            self.client = hvac.Client(url=url, token=token)
            self._available = True
        except ImportError:
            self.client = None
            self._available = False
            logger.warning("HashiCorp Secret Provider initialized but 'hvac' missing. Install 'nl2sql-core[hashicorp]'.")

    def get_secret(self, key: str) -> Optional[str]:
        if not self._available or not self.client:
             raise ImportError("HashiCorp Secret Provider is not available.")
             
        try:
            # key is assumed to be path/to/secret:key or just path (if single value?)
            # Let's assume standard kv v2: "secret/data/my-app:password"
            # Format: "mount/path:key"
            
            if ":" in key:
                path, field = key.split(":", 1)
            else:
                path = key
                field = "value" # Default field?

            # Using KV V2
            response = self.client.secrets.kv.v2.read_secret_version(path=path)
            data = response['data']['data']
            return data.get(field)
            
        except Exception as e:
            logger.error(f"Failed to fetch secret '{key}' from Vault: {e}")
            return None
