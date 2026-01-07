
import pytest
import sys
import os
from unittest.mock import MagicMock, patch

from nl2sql.secrets.manager import SecretManager
from nl2sql.secrets.providers.aws import AwsSecretProvider
from nl2sql.secrets.providers.azure import AzureSecretProvider

def test_secret_manager_routing():
    """Verifies that SecretManager correctly routes secrets to registered providers.
    
    Ensures that a secret reference in the format '${scheme:key}' is parsed
    and the 'key' is passed to the provider registered under 'scheme'.
    """
    mgr = SecretManager()
    mock_provider = MagicMock()
    mock_provider.get_secret.return_value = "routed_secret"
    
    mgr.register_provider("mock", mock_provider)
    
    result = mgr.resolve("${mock:db_pass}")
    assert result == "routed_secret"
    mock_provider.get_secret.assert_called_with("db_pass")

def test_default_env_provider():
    """Verifies that the default environment provider resolves secrets from os.environ.
    
    Tests that '${env:VAR}' effectively retrieves the value of VAR from the
    environment variables. Also asserts that the legacy implicit syntax is
    no longer supported.
    """
    with patch.dict(os.environ, {"TEST_VAR": "env_value"}):
        mgr = SecretManager()
        assert mgr.resolve("${env:TEST_VAR}") == "env_value"
        
        with pytest.raises(ValueError):
             mgr.resolve("${TEST_VAR}")

def test_aws_provider_success():
    """Verifies that AwsSecretProvider fetches secrets when dependencies are met.
    
    Mocks 'boto3' to simulate a successful connection and secret retrieval
    from AWS Secrets Manager.
    """
    mock_boto = MagicMock()
    mock_client = MagicMock()
    mock_boto.client.return_value = mock_client
    mock_client.get_secret_value.return_value = {"SecretString": "aws_secret_val"}
    
    with patch.dict(sys.modules, {"boto3": mock_boto}):
        provider = AwsSecretProvider()
        val = provider.get_secret("prod/db")
        assert val == "aws_secret_val"
        mock_boto.client.assert_called_with("secretsmanager")
        mock_client.get_secret_value.assert_called_with(SecretId="prod/db")

def test_aws_provider_missing_dep():
    """Verifies that AwsSecretProvider handles missing dependencies gracefully.
    
    Ensures that the provider initializes without error even if 'boto3' is
    missing, but raises an ImportError when a secret fetch is attempted.
    """
    with patch.dict(sys.modules, {"boto3": None}):
        provider = AwsSecretProvider()
        
        assert provider.client is None
        
        with pytest.raises(ImportError) as exc:
            provider.get_secret("foo")
        assert "'boto3' is not installed" in str(exc.value)

def test_azure_provider_success():
    """Verifies that AzureSecretProvider successfully retrieves secrets.
    
    Mocks 'azure.identity' and 'azure.keyvault.secrets' to simulate 
    successful secret retrieval from Azure Key Vault.
    """
    mock_identity = MagicMock()
    mock_secrets = MagicMock()
    mock_client_cls = MagicMock()
    mock_client_instance = MagicMock()
    
    mock_secrets.SecretClient.return_value = mock_client_instance
    mock_client_instance.get_secret.return_value.value = "azure_val"
    
    with patch.dict(sys.modules, {
        "azure.identity": mock_identity,
        "azure.keyvault.secrets": mock_secrets
    }):
        with patch.dict(os.environ, {"AZURE_KEYVAULT_URL": "https://vault.azure.net"}):
            provider = AzureSecretProvider()
            val = provider.get_secret("my-secret")
            assert val == "azure_val"
