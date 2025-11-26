"""
Lightweight golden query runner for the SQLite manufacturing DB.
Executes known-good SQL and asserts rows are returned.
"""
import pathlib
import sys

# Allow running without packaging
ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from src.datasource_config import get_profile, load_profiles
from src.engine_factory import make_engine, run_read_query


GOLDEN_QUERIES = [
    (
        "Production and scrap by product (fixed window)",
        """
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
    ),
    (
        "Defects by type and severity for Widget Alpha",
        """
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
    ),
    (
        "Maintenance downtime per machine (fixed window)",
        """
        SELECT
          m.name AS machine,
          SUM(l.downtime_minutes) AS downtime_minutes
        FROM maintenance_logs l
        JOIN machines m ON m.id = l.machine_id
        WHERE l.performed_at >= '2024-04-06T00:00:00'
        GROUP BY m.name
        ORDER BY downtime_minutes DESC;
        """,
    ),
    (
        "On-hand inventory per product and warehouse",
        """
        SELECT
          p.sku,
          p.name,
          i.warehouse,
          i.on_hand,
          i.updated_at
        FROM inventory i
        JOIN products p ON p.id = i.product_id
        ORDER BY p.sku, i.warehouse;
        """,
    ),
]


def main() -> None:
    profiles = load_profiles(ROOT / "configs" / "datasources.example.yaml")
    profile = get_profile(profiles, "manufacturing_sqlite")
    engine = make_engine(profile)

    for label, sql in GOLDEN_QUERIES:
        rows = run_read_query(engine, sql, row_limit=profile.row_limit)
        assert len(rows) > 0, f"No rows returned for '{label}'"
        print(f"[OK] {label} → {len(rows)} rows")


if __name__ == "__main__":
    main()
