from typing import Optional, Protocol

class SecretProvider(Protocol):
    """Protocol for fetching secrets from secure storage."""
    
    def get_secret(self, key: str) -> Optional[str]:
        """
        Retrieve a secret by its key.
        
        Args:
            key (str): The identifier for the secret (e.g., 'DB_PASSWORD').
            
        Returns:
            Optional[str]: The secret value, or None if not found.
        """
        ...
