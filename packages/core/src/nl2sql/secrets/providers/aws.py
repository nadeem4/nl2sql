from typing import Optional
import logging

logger = logging.getLogger(__name__)

class AwsSecretProvider:
    """Fetches secrets from AWS Secrets Manager."""
    
    def __init__(self, region_name: Optional[str] = None, profile_name: Optional[str] = None):
        try:
            import boto3
            session = boto3.Session(profile_name=profile_name, region_name=region_name)
            self.client = session.client('secretsmanager')
        except ImportError:
            self.client = None
            logger.warning("AWS Secret Provider initialized but 'boto3' is missing. Please install 'nl2sql-core[aws]' to use AWS secrets.")

    def get_secret(self, key: str) -> Optional[str]:
        if not self.client:
            raise ImportError("Cannot fetch AWS secret: 'boto3' is not installed.")
            
        try:
            # key is the SecretId
            response = self.client.get_secret_value(SecretId=key)
            if 'SecretString' in response:
                return response['SecretString']
            return None # Binary secrets not currently supported for connection strings
        except Exception as e:
            logger.error(f"Failed to fetch secret '{key}' from AWS: {e}")
            return None
