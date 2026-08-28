import os
from typing import Optional

class EnvironmentSecretProvider:
    """Fetches secrets from environment variables."""
    
    def get_secret(self, key: str) -> Optional[str]:
        return os.environ.get(key)
