from typing import List, Dict, Tuple

from nl2sql.datasources import DatasourceRegistry, DatasourceConfig
from nl2sql_adapter_sdk.contracts import AdapterRequest
from nl2sql.common.logger import get_logger
from nl2sql.secrets import SecretManager

logger = get_logger("diagnostics")

def check_connectivity(profiles: List[DatasourceConfig]) -> Dict[str, Tuple[bool, str]]:
    """
    Checks connectivity for a list of datasource profiles.

    Args:
        profiles (List[DatasourceConfig]): List of datasource configs.
    
    Returns:
        Dict[str, Tuple[bool, str]]: Map of ID -> (Success, Message).
    """
    registry = DatasourceRegistry(SecretManager())
    registry.register_datasources(profiles)
    results = {}

    for profile in profiles:
        ds_id = profile.id
        try:
            adapter = registry.get_adapter(ds_id)
            # Adapter Execute ping
            adapter.execute(AdapterRequest(plan_type="sql", payload={"sql": "SELECT 1"}))
            results[ds_id] = (True, "OK")
        except Exception as e:
            logger.error(f"Connectivity check failed for {ds_id}: {e}")
            results[ds_id] = (False, str(e))
            
    return results
