import importlib
from importlib.metadata import entry_points
from typing import Dict, Type
from nl2sql_adapter_sdk import DatasourceAdapter
from nl2sql.common.logger import get_logger

logger = get_logger(__name__)

def discover_adapters() -> Dict[str, Type[DatasourceAdapter]]:
    """
    Discovers installed adapters via 'nl2sql.adapters' entry points.
    
    Returns:
        Dict mapping adapter name (e.g., 'postgres') to the Adapter Class.
    """
    adapters = {}
    try:
        eps = entry_points(group="nl2sql.adapters")
    except TypeError:
        eps = entry_points().get("nl2sql.adapters", [])
    for ep in eps:
        try:
            AdapterCls = ep.load()
            adapters[ep.name] = AdapterCls
        except Exception as e:
            logger.error(f"Failed to load adapter {ep.name}: {e}")
            
    return adapters
