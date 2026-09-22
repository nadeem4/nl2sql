"""The two generated demo databases match a fresh generation, and hold the shapes we claim.

``scripts/generate_demo_datasets.py`` is deterministic: a fixed seed, a fixed
customer ordering and an explicit VACUUM. Regenerating into a temporary folder
must therefore reproduce the committed files byte for byte -- if it does not,
either the script changed without the databases being regenerated, or the
generation is no longer deterministic. Both should fail here.
"""
from __future__ import annotations

import hashlib
import importlib.util
import pathlib
import sqlite3
import sys

import pytest

from nl2sql.datasets import CHINOOK_DB_PATH, SUPPORT_DB_PATH, WEBANALYTICS_DB_PATH

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
SCRIPT = REPO_ROOT / "scripts" / "generate_demo_datasets.py"

# About 1 MB each is the budget: the demo image should not grow by much.
SIZE_BUDGET_BYTES = 1_000_000


def _load_generator():
    spec = importlib.util.spec_from_file_location("generate_demo_datasets", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def generator():
    if not SCRIPT.exists():
        pytest.skip(f"the generator is not in this tree: {SCRIPT}")
    return _load_generator()


@pytest.fixture(scope="module")
def regenerated(generator, tmp_path_factory) -> dict[str, pathlib.Path]:
    target = tmp_path_factory.mktemp("regenerated")
    generator.generate(target, CHINOOK_DB_PATH)
    return {path.name: path for path in target.iterdir()}


def _sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("committed", [SUPPORT_DB_PATH, WEBANALYTICS_DB_PATH],
                         ids=lambda p: p.stem)
def test_the_committed_database_matches_a_fresh_generation(committed, regenerated):
    fresh = regenerated[committed.name]
    assert _sha256(fresh) == _sha256(committed), (
        f"{committed.name} differs from a fresh generation. Re-run "
        "`python scripts/generate_demo_datasets.py` and commit the result."
    )


@pytest.mark.parametrize("path", [SUPPORT_DB_PATH, WEBANALYTICS_DB_PATH],
                         ids=lambda p: p.stem)
def test_the_generated_databases_stay_small(path):
    assert path.stat().st_size < SIZE_BUDGET_BYTES


@pytest.mark.parametrize("path", [CHINOOK_DB_PATH, SUPPORT_DB_PATH, WEBANALYTICS_DB_PATH],
                         ids=lambda p: p.stem)
def test_every_demo_database_is_packaged(path):
    assert path.is_file()


def _rows(path: pathlib.Path, sql: str):
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def test_the_generated_databases_share_chinook_customer_identities():
    """Every customer email in support and webanalytics is a Chinook customer."""
    chinook = {row[0] for row in _rows(CHINOOK_DB_PATH, "SELECT Email FROM Customer")}
    assert len(chinook) == 59

    support = {row[0] for row in _rows(SUPPORT_DB_PATH, "SELECT customer_email FROM customers")}
    assert support == chinook

    signed_in = {row[0] for row in _rows(
        WEBANALYTICS_DB_PATH,
        "SELECT DISTINCT customer_email FROM sessions WHERE customer_email IS NOT NULL")}
    assert signed_in <= chinook
    assert signed_in, "no session is attributed to a customer"


def test_customer_ids_line_up_with_chinook():
    by_email = dict(_rows(CHINOOK_DB_PATH, "SELECT Email, CustomerId FROM Customer"))
    for email, customer_id in _rows(SUPPORT_DB_PATH, "SELECT customer_email, customer_id FROM customers"):
        assert by_email[email] == customer_id
    for email, customer_id in _rows(
            WEBANALYTICS_DB_PATH,
            "SELECT DISTINCT customer_email, customer_id FROM sessions WHERE customer_email IS NOT NULL"):
        assert by_email[email] == customer_id


def test_the_dates_sit_inside_chinooks_invoice_period():
    low, high = _rows(CHINOOK_DB_PATH, "SELECT MIN(InvoiceDate), MAX(InvoiceDate) FROM Invoice")[0]
    for path, sql in (
        (SUPPORT_DB_PATH, "SELECT MIN(opened_at), MAX(opened_at) FROM tickets"),
        (WEBANALYTICS_DB_PATH, "SELECT MIN(started_at), MAX(started_at) FROM sessions"),
    ):
        first, last = _rows(path, sql)[0]
        assert low <= first and last <= high, path.name


def test_support_has_a_spread_of_statuses_and_the_awkward_cases():
    statuses = dict(_rows(SUPPORT_DB_PATH, "SELECT status, COUNT(*) FROM tickets GROUP BY 1"))
    assert {"open", "pending", "escalated", "resolved", "closed"} == set(statuses)
    assert all(count > 5 for count in statuses.values())

    assert _rows(SUPPORT_DB_PATH, """
        SELECT COUNT(*) FROM customers c
        WHERE NOT EXISTS (SELECT 1 FROM tickets t WHERE t.customer_id = c.customer_id)
    """)[0][0] > 0, "every customer has a ticket, so 'no tickets' has no answer"

    assert _rows(SUPPORT_DB_PATH, """
        SELECT COUNT(*) FROM tickets t
        WHERE NOT EXISTS (SELECT 1 FROM ticket_messages m WHERE m.ticket_id = t.ticket_id)
    """)[0][0] == 1, "the ticket with no messages is missing"

    assert _rows(SUPPORT_DB_PATH,
                 "SELECT COUNT(*) FROM tickets WHERE closed_at IS NOT NULL AND closed_at < opened_at"
                 )[0][0] == 0


def test_webanalytics_has_anonymous_traffic_and_single_view_sessions():
    assert _rows(WEBANALYTICS_DB_PATH,
                 "SELECT COUNT(*) FROM sessions WHERE customer_email IS NULL")[0][0] > 0

    assert _rows(WEBANALYTICS_DB_PATH, """
        SELECT COUNT(*) FROM (
            SELECT session_id FROM page_views GROUP BY session_id HAVING COUNT(*) = 1
        )
    """)[0][0] > 0, "no single-page-view session"

    # Some customers never visit at all.
    visitors = _rows(WEBANALYTICS_DB_PATH,
                     "SELECT COUNT(DISTINCT customer_id) FROM sessions WHERE customer_id IS NOT NULL")[0][0]
    assert visitors < 59

    # A conversion always belongs to a session that exists.
    assert _rows(WEBANALYTICS_DB_PATH, """
        SELECT COUNT(*) FROM conversions c
        WHERE NOT EXISTS (SELECT 1 FROM sessions s WHERE s.session_id = c.session_id)
    """)[0][0] == 0


def test_the_pricing_page_question_has_an_answer():
    """Visited /pricing but never purchased: a real, non-empty, non-total set."""
    viewed, bought = _rows(WEBANALYTICS_DB_PATH, """
        SELECT
          (SELECT COUNT(DISTINCT s.customer_email) FROM sessions s
             JOIN page_views p ON p.session_id = s.session_id
            WHERE p.page_path = '/pricing' AND s.customer_email IS NOT NULL),
          (SELECT COUNT(DISTINCT customer_email) FROM conversions
            WHERE event_type = 'purchase' AND customer_email IS NOT NULL)
    """)[0]
    assert 0 < bought < viewed
