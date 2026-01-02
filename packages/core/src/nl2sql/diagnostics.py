from typing import List, Dict, Tuple
from sqlalchemy import text
from nl2sql.datasources import DatasourceRegistry, DatasourceProfile
from nl2sql.common.logger import get_logger

logger = get_logger("diagnostics")

def check_connectivity(profiles: List[DatasourceProfile]) -> Dict[str, Tuple[bool, str]]:
    """
    Checks connectivity for a list of datasource profiles.
    Returns a dict mapping datasource_id -> (Success, Message).
    """
    registry = DatasourceRegistry(profiles)
    results = {}

    for profile in profiles:
        ds_id = profile.id
        try:
            adapter = registry.get_adapter(ds_id)
            # SQLAlchemy text ping
            with adapter.get_connection() as conn:
                conn.execute(text("SELECT 1"))
            results[ds_id] = (True, "OK")
        except Exception as e:
            logger.error(f"Connectivity check failed for {ds_id}: {e}")
            results[ds_id] = (False, str(e))
            
    return results
