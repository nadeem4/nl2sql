import importlib
from importlib.metadata import entry_points
from typing import Dict, Type
from nl2sql_adapter_sdk import DatasourceAdapter

def discover_adapters() -> Dict[str, Type[DatasourceAdapter]]:
    """
    Discovers installed adapters via 'nl2sql.adapters' entry points.
    
    Returns:
        Dict mapping adapter name (e.g., 'postgres') to the Adapter Class.
    """
    adapters = {}
    # Python 3.10+ usage select, prior usages dictionary access
    try:
        eps = entry_points(group="nl2sql.adapters")
    except TypeError:
        # Fallback for older python
        eps = entry_points().get("nl2sql.adapters", [])
        
    for ep in eps:
        try:
            AdapterCls = ep.load()
            adapters[ep.name] = AdapterCls
        except Exception as e:
            print(f"Failed to load adapter {ep.name}: {e}")
            
    return adapters
