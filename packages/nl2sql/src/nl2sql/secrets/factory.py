from .interfaces import SecretProvider
from nl2sql.common.settings import secret_value
from .models import SecretProviderConfig, AwsSecretConfig, AzureSecretConfig, HashiCorpSecretConfig

class SecretProviderFactory:
    """Factory for creating SecretProvider instances.
    
    Centralizes the logic for:
    1. Dynamic imports (lazy loading of heavy dependencies).
    2. Dependency checking (Boto3, Azure SDK, etc.).
    3. Instantiation with or without explicit config.
    """

    @staticmethod
    def create(config: SecretProviderConfig) -> SecretProvider:
        """Creates a provider instance from configuration.
        
        Args:
            config: A fully resolved configuration object (no placeholders).
        
        Returns:
            The instantiated SecretProvider.

        Raises:
            ImportError: The provider's optional package is not installed; the
                message names the extra to install.
            ValueError: The provider type is unknown.
            Exception: Whatever the provider raised while starting.
        """
        if isinstance(config, AwsSecretConfig):
            return SecretProviderFactory._create_aws(config)
        if isinstance(config, AzureSecretConfig):
            return SecretProviderFactory._create_azure(config)
        if isinstance(config, HashiCorpSecretConfig):
            return SecretProviderFactory._create_hashi(config)
        if config.type == "env":
            # Env provider is usually bootstrapped, but we support creating it explicitly too
            from .providers.env import EnvironmentSecretProvider
            return EnvironmentSecretProvider()
        raise ValueError(f"Unknown secret provider type '{config.type}'.")

    @staticmethod
    def _create_aws(config: AwsSecretConfig) -> SecretProvider:
        try:
            from .providers.aws import AwsSecretProvider
        except ImportError:
             raise ImportError("Missing dependency 'boto3'. Install 'nl2sql-engine[aws]'.")
        
        return AwsSecretProvider(
            region_name=config.region_name,
            profile_name=config.profile_name
        )

    @staticmethod
    def _create_azure(config: AzureSecretConfig) -> SecretProvider:
        try:
            from .providers.azure import AzureSecretProvider
        except ImportError:
            raise ImportError("Missing dependencies. Install 'nl2sql-engine[azure]'.")

        return AzureSecretProvider(
            vault_url=config.vault_url,
            client_id=config.client_id,
            client_secret=secret_value(config.client_secret),
            tenant_id=config.tenant_id
        )

    @staticmethod
    def _create_hashi(config: HashiCorpSecretConfig) -> SecretProvider:
        try:
            from .providers.hashi import HashiCorpSecretProvider
        except ImportError:
            raise ImportError("Missing dependency 'hvac'. Install 'nl2sql-engine[hashicorp]'.")
            
        return HashiCorpSecretProvider(
            url=config.url,
            token=secret_value(config.token),
            mount_point=config.mount_point
        )
