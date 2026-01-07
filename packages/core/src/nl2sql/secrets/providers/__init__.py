from typing import TYPE_CHECKING
import logging

if TYPE_CHECKING:
    from nl2sql.secrets.manager import SecretManager

logger = logging.getLogger(__name__)

def register_available_providers(manager: "SecretManager") -> None:
    """
    Attempts to import and register cloud secret providers.
    Fails silently (with debug log) if dependencies are missing.
    """
    
    # AWS
    try:
        import boto3
        from .aws import AwsSecretProvider
        manager.register_provider("aws", AwsSecretProvider())
        logger.debug("Registered 'aws' secret provider.")
    except ImportError:
        logger.debug("Skipping 'aws' provider: dependencies missing.")

    # Azure
    try:
        import azure.identity
        import azure.keyvault.secrets
        from .azure import AzureSecretProvider
        manager.register_provider("azure", AzureSecretProvider())
        logger.debug("Registered 'azure' secret provider.")
    except ImportError:
        logger.debug("Skipping 'azure' provider: dependencies missing.")

    # HashiCorp
    try:
        import hvac
        from .hashi import HashiCorpSecretProvider
        manager.register_provider("hashi", HashiCorpSecretProvider())
        logger.debug("Registered 'hashi' secret provider.")
    except ImportError:
        logger.debug("Skipping 'hashi' provider: dependencies missing.")
