from .manager import secret_manager, SecretManager
from .interfaces import SecretProvider


# Auto-register available cloud providers
from .config import load_secret_configs
from pathlib import Path
import os
import logging

logger = logging.getLogger(__name__)


__all__ = ["secret_manager", "SecretManager", "SecretProvider"]
