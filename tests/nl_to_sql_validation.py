"""
NL → SQL validation harness for the SQLite manufacturing DB.
Takes a mapping of NL to expected SQL and asserts the SQL runs and returns rows.
"""
import pathlib
import sys
from typing import Dict

# Allow running without packaging
ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from src.datasource_config import get_profile, load_profiles
from src.engine_factory import make_engine, run_read_query


GOLDENS: Dict[str, str] = {
    "Show total quantity produced and scrap for each product in the last 2 days.": """
        SELECT
          p.sku,
          p.name,
          SUM(r.quantity_produced) AS total_qty,
          SUM(r.scrap_count) AS total_scrap
        FROM production_runs r
        JOIN products p ON p.id = r.product_id
        WHERE r.started_at >= '2024-04-08T00:00:00'
        GROUP BY p.sku, p.name
        ORDER BY total_qty DESC;
    """,
    "List defects by type and severity for Widget Alpha.": """
        SELECT
          d.defect_type,
          d.severity,
          SUM(d.defect_count) AS total_defects
        FROM defects d
        JOIN production_runs r ON r.id = d.production_run_id
        JOIN products p ON p.id = r.product_id
        WHERE p.name = 'Widget Alpha'
        GROUP BY d.defect_type, d.severity
        ORDER BY total_defects DESC;
    """,
}


def main() -> None:
    profiles = load_profiles(ROOT / "configs" / "datasources.example.yaml")
    profile = get_profile(profiles, "manufacturing_sqlite")
    engine = make_engine(profile)

    for nl, sql in GOLDENS.items():
        rows = run_read_query(engine, sql, row_limit=profile.row_limit)
        assert len(rows) > 0, f"No rows returned for NL: {nl}"
        print(f"[OK] {nl} → {len(rows)} rows")


if __name__ == "__main__":
    main()
