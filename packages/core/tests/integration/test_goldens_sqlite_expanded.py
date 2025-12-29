import pathlib
import sys # Allow running without packaging
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.append(str(SRC))

from nl2sql.datasources import get_profile, load_profiles
from nl2sql.engine_factory import make_engine, run_read_query


GOLDENS = [
    (
        "Factory totals for Widgets",
        """
        SELECT
          f.name AS factory,
          SUM(r.quantity_produced) AS total_qty,
          SUM(r.scrap_count) AS total_scrap
        FROM production_runs r
        JOIN machines m ON m.id = r.machine_id
        JOIN factories f ON f.id = m.factory_id
        JOIN products p ON p.id = r.product_id
        WHERE p.category = 'Widgets'
        GROUP BY f.name
        HAVING SUM(r.quantity_produced) > 0
        ORDER BY total_qty DESC;
        """,
    ),
    (
        "Maintenance over 60 minutes",
        """
        SELECT
          m.name AS machine,
          l.maintenance_type,
          l.downtime_minutes,
          l.performed_at
        FROM maintenance_logs l
        JOIN machines m ON m.id = l.machine_id
        WHERE l.downtime_minutes > 60
        ORDER BY l.downtime_minutes DESC;
        """,
    ),
    (
        "Top defects by product",
        """
        SELECT
          p.sku,
          p.name,
          d.defect_type,
          SUM(d.defect_count) AS total_defects
        FROM defects d
        JOIN production_runs r ON r.id = d.production_run_id
        JOIN products p ON p.id = r.product_id
        GROUP BY p.sku, p.name, d.defect_type
        HAVING SUM(d.defect_count) > 0
        ORDER BY total_defects DESC;
        """,
    ),
    (
        "Production runs with date filter and order",
        """
        SELECT
          r.id,
          p.sku,
          p.name,
          r.started_at,
          r.ended_at,
          r.quantity_produced,
          r.scrap_count
        FROM production_runs r
        JOIN products p ON p.id = r.product_id
        WHERE p.category = 'Widgets'
          AND r.started_at >= '2024-04-09T00:00:00'
        ORDER BY r.started_at ASC
        LIMIT 50;
        """,
    ),
    (
        "Machines commissioned after 2013 with maintenance > 60",
        """
        SELECT
          m.name,
          m.line,
          m.commissioned_on,
          l.maintenance_type,
          l.downtime_minutes
        FROM machines m
        JOIN maintenance_logs l ON l.machine_id = m.id
        WHERE m.commissioned_on > '2013-01-01'
          AND l.downtime_minutes > 60
        ORDER BY l.downtime_minutes DESC;
        """,
    ),
    (
        "Inventory updated after date by warehouse",
        """
        SELECT
          i.warehouse,
          p.sku,
          p.name,
          i.on_hand,
          i.updated_at
        FROM inventory i
        JOIN products p ON p.id = i.product_id
        WHERE i.updated_at > '2024-04-09T00:00:00'
        ORDER BY i.warehouse, p.sku;
        """,
    ),
    (
        "Average maintenance downtime per factory",
        """
        SELECT
          f.name AS factory,
          AVG(l.downtime_minutes) AS avg_downtime
        FROM maintenance_logs l
        JOIN machines m ON m.id = l.machine_id
        JOIN factories f ON f.id = m.factory_id
        WHERE l.downtime_minutes > 30
        GROUP BY f.name
        HAVING AVG(l.downtime_minutes) > 0
        ORDER BY avg_downtime DESC;
        """,
    ),
    (
        "Production runs for Widget Alpha with machine and factory",
        """
        SELECT
          r.id,
          p.name AS product,
          m.name AS machine,
          f.name AS factory,
          r.started_at,
          r.quantity_produced
        FROM production_runs r
        JOIN products p ON p.id = r.product_id
        JOIN machines m ON m.id = r.machine_id
        JOIN factories f ON f.id = m.factory_id
        WHERE p.name = 'Widget Alpha'
        ORDER BY r.started_at DESC;
        """,
    ),
]


@pytest.fixture(scope="session")
def profile():
    profiles = load_profiles(ROOT / "configs" / "datasources.yaml")
    return get_profile(profiles, "manufacturing_sqlite")


@pytest.fixture(scope="session")
def engine(profile):
    return make_engine(profile)


@pytest.mark.parametrize("label, sql", GOLDENS)
def test_expanded_goldens(engine, profile, label, sql):
    rows = run_read_query(engine, sql, row_limit=profile.row_limit)
    assert len(rows) > 0, f"No rows returned for: {label}"
