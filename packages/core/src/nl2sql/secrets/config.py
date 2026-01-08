
from typing import List
import pathlib
from pydantic import TypeAdapter
from .schemas import SecretProviderConfig
def load_secret_configs(path: pathlib.Path) -> List[SecretProviderConfig]:
    """Loads secret provider configurations from a YAML file.

    Args:
        path (pathlib.Path): Path to the secrets.yaml file.

    Returns:
        List[SecretProviderConfig]: A list of validated provider configurations.
    """
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("PyYAML is required to load secret configs") from exc

    if not path.exists():
        return []

    content = path.read_text(encoding="utf-8")
    raw = yaml.safe_load(content)
    
    if not raw:
        return []
    
    if isinstance(raw, dict) and "providers" in raw:
        raw = raw["providers"] or []

    if not isinstance(raw, list):
        raise ValueError(f"Invalid config structure in {path}. Expected 'providers' list.")

    adapter = TypeAdapter(List[SecretProviderConfig])
    return adapter.validate_python(raw)
