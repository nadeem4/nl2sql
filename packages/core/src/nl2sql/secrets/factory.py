from typing import Optional, Any, Dict, Type
import logging
from .interfaces import SecretProvider
from .schemas import SecretProviderConfig, AwsSecretConfig, AzureSecretConfig, HashiCorpSecretConfig

logger = logging.getLogger(__name__)

class SecretProviderFactory:
    """Factory for creating SecretProvider instances.
    
    Centralizes the logic for:
    1. Dynamic imports (lazy loading of heavy dependencies).
    2. Dependency checking (Boto3, Azure SDK, etc.).
    3. Instantiation with or without explicit config.
    """

    @staticmethod
    def create(config: SecretProviderConfig) -> Optional[SecretProvider]:
        """Creates a provider instance from configuration.
        
        Args:
            config: A fully resolved configuration object (no placeholders).
        
        Returns:
            The instantiated SecretProvider, or None if dependencies are missing.
        """
        try:
            if isinstance(config, AwsSecretConfig):
                return SecretProviderFactory._create_aws(config)
            elif isinstance(config, AzureSecretConfig):
                return SecretProviderFactory._create_azure(config)
            elif isinstance(config, HashiCorpSecretConfig):
                return SecretProviderFactory._create_hashi(config)
            elif config.type == "env":
                # Env provider is usually bootstrapped, but we support creating it explicitly too
                from .providers.env import EnvironmentSecretProvider
                return EnvironmentSecretProvider()
            else:
                logger.warning(f"Unknown provider type: {config.type}")
                return None
        except ImportError as e:
            logger.warning(f"Skipping provider '{config.id}' ({config.type}): {e}")
            return None
        except Exception as e:
            logger.error(f"Failed to create provider '{config.id}': {e}")
            return None

    @staticmethod
    def _create_aws(config: AwsSecretConfig) -> SecretProvider:
        try:
            from .providers.aws import AwsSecretProvider
        except ImportError:
             raise ImportError("Missing dependency 'boto3'. Install 'nl2sql-core[aws]'.")
        
        return AwsSecretProvider(
            region_name=config.region_name,
            profile_name=config.profile_name
        )

    @staticmethod
    def _create_azure(config: AzureSecretConfig) -> SecretProvider:
        try:
            from .providers.azure import AzureSecretProvider
        except ImportError:
            raise ImportError("Missing dependencies. Install 'nl2sql-core[azure]'.")

        return AzureSecretProvider(
            vault_url=config.vault_url,
            client_id=config.client_id,
            client_secret=config.client_secret,
            tenant_id=config.tenant_id
        )

    @staticmethod
    def _create_hashi(config: HashiCorpSecretConfig) -> SecretProvider:
        try:
            from .providers.hashi import HashiCorpSecretProvider
        except ImportError:
            raise ImportError("Missing dependency 'hvac'. Install 'nl2sql-core[hashicorp]'.")
            
        return HashiCorpSecretProvider(
            url=config.url,
            token=config.token,
            mount_point=config.mount_point
        )
