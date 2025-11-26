import pathlib
import sys

import pytest

# Allow running without packaging
ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.append(str(SRC))

from datasource_config import get_profile, load_profiles
from engine_factory import make_engine, run_read_query


GOLDENS = [
    (
        "Show total quantity produced and scrap for each product in the last 2 days.",
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
        "List defects by type and severity for Widget Alpha.",
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
]


@pytest.fixture(scope="session")
def profile():
    profiles = load_profiles(ROOT / "configs" / "datasources.example.yaml")
    return get_profile(profiles, "manufacturing_sqlite")


@pytest.fixture(scope="session")
def engine(profile):
    return make_engine(profile)


@pytest.mark.parametrize("nl, sql", GOLDENS)
def test_goldens_return_rows(engine, profile, nl, sql):
    rows = run_read_query(engine, sql, row_limit=profile.row_limit)
    assert len(rows) > 0, f"No rows returned for NL: {nl}"
