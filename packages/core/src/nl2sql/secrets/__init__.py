from .manager import SecretManager
from .interfaces import SecretProvider
from .models import SecretProviderConfig

from pathlib import Path
import os
import logging

logger = logging.getLogger(__name__)


secret_manager = SecretManager()

__all__ = ["SecretManager", "SecretProvider", "SecretProviderConfig", "secret_manager"]
