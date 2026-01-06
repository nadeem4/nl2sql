from typing import List, Dict, Tuple
from nl2sql.datasources import DatasourceRegistry, DatasourceProfile
from nl2sql.common.logger import get_logger

logger = get_logger("diagnostics")

def check_connectivity(profiles: List[DatasourceProfile]) -> Dict[str, Tuple[bool, str]]:
    """
    Checks connectivity for a list of datasource profiles.

    Args:
        profiles (List[DatasourceProfile]): List of datasource profiles.
    
    Returns:
        Dict[str, Tuple[bool, str]]: Map of ID -> (Success, Message).
    """
    # Convert list to dict for Registry
    profile_map = {p.id: p for p in profiles}
    registry = DatasourceRegistry(profile_map)
    results = {}

    for profile in profiles:
        ds_id = profile.id
        try:
            adapter = registry.get_adapter(ds_id)
            # Adapter Execute ping
            adapter.execute("SELECT 1")
            results[ds_id] = (True, "OK")
        except Exception as e:
            logger.error(f"Connectivity check failed for {ds_id}: {e}")
            results[ds_id] = (False, str(e))
            
    return results
