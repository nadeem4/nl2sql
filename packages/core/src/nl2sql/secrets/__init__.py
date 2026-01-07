from .manager import secret_manager, SecretManager
from .interfaces import SecretProvider
from .providers import register_available_providers

# Auto-register available cloud providers
register_available_providers(secret_manager)

__all__ = ["secret_manager", "SecretManager", "SecretProvider"]
