import re
import os
import pathlib
from typing import Any, Dict, List, Optional, Union
from nl2sql.secrets import secret_manager
from nl2sql.secrets import secret_manager



def _resolve_secrets(obj: Any) -> Any:
    """Recursively resolves secrets in a dictionary or list."""
    if isinstance(obj, dict):
        return {k: _resolve_secrets(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_resolve_secrets(i) for i in obj]
    elif isinstance(obj, str):
        # Using regex-based resolution from secret manager for ${key}
        # Note: secret_manager.resolve might already handle full strings
        return secret_manager.resolve(obj)
    return obj

def load_configs(path: pathlib.Path) -> List[Dict[str, Any]]:
    """Loads datasource configurations from a YAML file.

    Returns:
        List[Dict[str, Any]]: A list of raw datasource setting dictionaries.
    """
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("PyYAML is required to load datasource configs") from exc

    if not path.exists():
        raise FileNotFoundError(f"Datasource config not found: {path}")

    content = path.read_text(encoding="utf-8")
    raw = yaml.safe_load(content)
    
    if isinstance(raw, dict) and "datasources" in raw:
        raw = raw["datasources"]

    if not isinstance(raw, list):
        raise ValueError(f"Invalid config structure in {path}. Expected 'datasources' list.")

    # Resolve Secrets Recursively
    resolved_configs = _resolve_secrets(raw)
    
    return resolved_configs



