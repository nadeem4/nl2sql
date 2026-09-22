"""Chinook demo dataset: datasource, policies and guided questions.

The database itself is vendored at ``nl2sql/datasets/chinook.sqlite`` and
is copied into the demo project by :meth:`DemoManager.setup_chinook`. See
``THIRD_PARTY_NOTICES.md`` at the repository root for its license.
"""

CHINOOK_DATASOURCE = {
    "id": "chinook",
    "connection": {"type": "sqlite", "database": "data/chinook.sqlite"},
    "description": "Chinook digital music store: artists, albums, tracks, genres, playlists, customers, employees, invoices and invoice lines",
}

_ALL = ["Album", "Artist", "Customer", "Employee", "Genre", "Invoice", "InvoiceLine", "MediaType", "Playlist", "PlaylistTrack", "Track"]
_CATALOG = ["Album", "Artist", "Genre", "MediaType", "Playlist", "PlaylistTrack", "Track"]

CHINOOK_POLICIES = {
    "admin": {"description": "Every table", "role": "admin", "allowed_datasources": ["*"], "allowed_tables": ["*"]},
    "analyst": {"description": "Everything except employee records", "role": "analyst", "allowed_datasources": ["chinook"],
                "allowed_tables": [f"chinook.{t}" for t in _ALL if t != "Employee"]},
    "viewer": {"description": "Music catalog only, no customers or sales", "role": "viewer", "allowed_datasources": ["chinook"],
               "allowed_tables": [f"chinook.{t}" for t in _CATALOG]},
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
