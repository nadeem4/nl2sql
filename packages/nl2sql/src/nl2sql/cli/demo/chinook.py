"""Chinook demo dataset: datasource, policies and guided questions.

The database itself is vendored at ``nl2sql/datasets/chinook.sqlite`` and
is copied into the demo project by :meth:`DemoManager.setup_demo`. See
``THIRD_PARTY_NOTICES.md`` at the repository root for its license.

Chinook is one of the demo's three databases; ``support`` and ``webanalytics``
sit beside it and share its customer identities. ``nl2sql.cli.demo.datasets``
gathers all three.
"""

CHINOOK_DATASOURCE = {
    "id": "chinook",
    # Read-only: nothing in the engine writes to a demo database, and a hosted
    # demo must not be able to. ``mode=ro`` makes the driver refuse a write
    # before any SQL is parsed (``SqliteAdapter.construct_uri``).
    "connection": {"type": "sqlite", "database": "data/chinook.sqlite",
                   "options": {"read_only": True}},
    "description": "Chinook digital music store: artists, albums, tracks, genres, playlists, customers, employees, invoices and invoice lines",
}

CHINOOK_ALL = ["Album", "Artist", "Customer", "Employee", "Genre", "Invoice", "InvoiceLine", "MediaType", "Playlist", "PlaylistTrack", "Track"]
_CATALOG = ["Album", "Artist", "Genre", "MediaType", "Playlist", "PlaylistTrack", "Track"]

# The tables each demo role may read. The same shape is defined for the other
# two demo databases, and ``nl2sql.cli.demo.datasets`` merges the three into
# the policy file the demo project gets.
CHINOOK_TABLES_BY_ROLE = {
    "analyst": [t for t in CHINOOK_ALL if t != "Employee"],
    "viewer": list(_CATALOG),
}

# Chinook on its own, as the tier 1 gold evaluation reads it. The demo project
# gets ``nl2sql.cli.demo.datasets.DEMO_POLICIES``, which covers all three
# databases and grants the same Chinook tables to each role.
CHINOOK_POLICIES = {
    "admin": {"description": "Every table", "role": "admin", "allowed_datasources": ["*"], "allowed_tables": ["*"]},
    "analyst": {"description": "Everything except employee records", "role": "analyst", "allowed_datasources": ["chinook"],
                "allowed_tables": [f"chinook.{t}" for t in CHINOOK_TABLES_BY_ROLE["analyst"]]},
    "viewer": {"description": "Music catalog only, no customers or sales", "role": "viewer", "allowed_datasources": ["chinook"],
               "allowed_tables": [f"chinook.{t}" for t in CHINOOK_TABLES_BY_ROLE["viewer"]]},
}

CHINOOK_QUESTIONS = [
    "How many customers do we have, by country?",
    "Who are the top 5 customers by total spend?",
    "Which artist has the most albums?",
    "What is the total revenue per year?",
    "Which genre sells the most tracks?",
    "What is the average invoice total by billing country?",
    "Which employees support the most customers?",
    "What is the longest track in each genre?",
    "Which customers bought jazz tracks but never rock?",
    "What was the monthly revenue in 2013 for customers in the USA?",
    "Which playlists contain tracks from more than three genres?",
    "Who are the top customers by total spend?",
]
