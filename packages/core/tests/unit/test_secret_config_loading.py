
import pytest
from unittest.mock import MagicMock, patch, mock_open
import pathlib
from nl2sql.secrets.config import load_secret_configs
from nl2sql.secrets.manager import SecretManager
from nl2sql.secrets.schemas import AwsSecretConfig, AzureSecretConfig, SecretProviderConfig

class TestSecretConfigLoading:

    def test_load_secret_configs_valid_yaml(self, tmp_path):
        """Test parsing valid secrets.yaml."""
        # Create a dummy secrets.yaml
        secrets_path = tmp_path / "secrets.yaml"
        secrets_path.write_text("""
        version: 1
        providers:
          - id: azure-prod
            type: azure
            vault_url: "https://foo"
          - id: aws-main
            type: aws
            region_name: "us-east-1"
        """, encoding="utf-8")

        # Act
        configs = load_secret_configs(secrets_path)

        # Assert
        assert len(configs) == 2
        assert isinstance(configs[0], AzureSecretConfig)
        assert configs[0].id == "azure-prod"
        assert configs[0].vault_url == "https://foo"
            
        assert isinstance(configs[1], AwsSecretConfig)
        assert configs[1].type == "aws"

    def test_secret_manager_configure_two_phase(self):
        """Test SecretManager.configure resolves values via env provider."""
        mgr = SecretManager()
        
        # Define a config that relies on an env var
        config = AzureSecretConfig(
            id="azure-test",
            type="azure", 
            vault_url="https://test",
            client_secret="${env:MY_SECRET}" # Needs resolution
        )
        
        # Mock the env provider to return a value
        # The manager initializes with 'env' by default
        with patch.dict("os.environ", {"MY_SECRET": "resolved-value"}):
            # We also need to mock AzureSecretProvider since we can't actually init it
            with patch("nl2sql.secrets.providers.azure.AzureSecretProvider") as MockAzure:
                 mgr.configure([config])
                 
                 # Verify AzureProvider was init with RESOLVED value
                 MockAzure.assert_called_once()
                 call_kwargs = MockAzure.call_args.kwargs
                 assert call_kwargs["client_secret"] == "resolved-value"
                 
                 # Verify it was registered
                 assert mgr.resolve("${azure-test:key}") is not None # Mock returns Mock object which returns Mock

if __name__ == "__main__":
    pytest.main([__file__])
