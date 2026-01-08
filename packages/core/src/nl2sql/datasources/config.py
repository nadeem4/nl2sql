from typing import Any, Dict, List, Optional, Union
import pathlib



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

    return raw



