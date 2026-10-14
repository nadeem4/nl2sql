from .manager import SecretManager
from .models import SecretProviderConfig

secret_manager = SecretManager()

__all__ = ["SecretManager", "SecretProviderConfig", "secret_manager"]
