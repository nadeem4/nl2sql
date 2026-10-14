"""Chinook demo dataset: datasource, policies and guided questions.

The database itself is vendored at ``nl2sql/datasets/chinook.sqlite``. See
``THIRD_PARTY_NOTICES.md`` at the repository root for its license.

Chinook is one of the demo's three databases; ``support`` and ``webanalytics``
sit beside it and share its customer identities. ``nl2sql.datasets.demo``
gathers all three, and ``nl2sql.config.demo_config`` builds the demo config
from them. The dicts here are in the shape of ``nl2sql.yaml``: a datasource is
a ``nl2sql.config.Datasource``, a policy a ``nl2sql.config.Role``.
"""

# The most rows one demo sub-query returns, for the local and the hosted
# playground alike: lower than the engine's RESULT_ROW_LIMIT, so a public demo
# stays light. A result that reaches it is flagged as truncated, and the
# answer says so.
DEMO_ROW_LIMIT = 10_000

CHINOOK_DATASOURCE = {
    "id": "chinook",
    "type": "sqlite",
    # Read-only: nothing in the engine writes to a demo database, and a hosted
    # demo must not be able to. ``mode=ro`` makes the driver refuse a write
    # before any SQL is parsed (``SqliteAdapter.construct_uri``). ``database``
    # is the packaged file's name; ``demo_config`` says which folder it is in.
    "connection": {"database": "chinook.sqlite", "options": {"read_only": True}},
    "limits": {"row_limit": DEMO_ROW_LIMIT},
    "description": "Chinook digital music store: artists, albums, tracks, genres, playlists, customers, employees, invoices and invoice lines",
}

_CATALOG = ["Album", "Artist", "Genre", "MediaType", "Playlist", "PlaylistTrack", "Track"]

# What each demo role may read in Chinook, as policy patterns. Every table is
# in SQLite's ``main`` schema. The analyst reads the whole database but the
# employee records; the viewer reads the music catalog and nothing else. The
# same shape is defined for the other two demo databases, and
# ``nl2sql.datasets.demo`` merges the three into the demo's policies.
CHINOOK_ROLES = {
    "analyst": {"allow": ["chinook"], "deny": ["chinook.main.Employee"]},
    "viewer": {"allow": [f"chinook.main.{t}" for t in _CATALOG]},
}

# Chinook on its own, as the tier 1 gold evaluation reads it. The demo project
# gets ``nl2sql.datasets.demo.DEMO_POLICIES``, which covers all three
# databases and grants the same Chinook tables to each role.
CHINOOK_POLICIES = {
    "admin": {"description": "Every table", "allow": ["*"]},
    "analyst": {"description": "Everything except employee records", **CHINOOK_ROLES["analyst"]},
    "viewer": {"description": "Music catalog only, no customers or sales", **CHINOOK_ROLES["viewer"]},
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
    "Which 5 customers bought the most jazz tracks?",
    "What was the monthly revenue in 2013 for customers in the USA?",
    "Which playlists contain tracks from more than three genres?",
    "Who are the top customers by total spend?",
]

# The demo's verified queries, its ``curated`` section (see
# docs/configuration/curated.md). Questions a visitor is likely to ask, each
# with several phrasings; the guided questions "What is the total revenue per
# year?" and "Which genre sells the most tracks?" are among them. Revenue is
# invoiced revenue (unit price times quantity, which equals Invoice.Total in
# Chinook). Invoices run from 2009 to 2013, hence the examples.
CHINOOK_VERIFIED_QUERIES = [
    {
        "id": "top_customers_by_revenue",
        "description": "Customers ranked by invoiced revenue in a calendar year",
        "questions": [
            "Who are our top {n} customers by revenue in {year}?",
            "Best {n} customers in {year}",
            "Which {n} customers spent the most in {year}?",
        ],
        "params": {
            "n": {"type": "int", "default": 10, "min": 1, "max": 100},
            "year": {"type": "int", "min": 2009, "max": 2013, "example": 2012},
        },
        "sql": (
            "SELECT c.CustomerId,\n"
            "       c.FirstName || ' ' || c.LastName AS customer,\n"
            "       c.Country,\n"
            "       ROUND(SUM(il.UnitPrice * il.Quantity), 2) AS revenue\n"
            "FROM Customer c\n"
            "JOIN Invoice i      ON i.CustomerId = c.CustomerId\n"
            "JOIN InvoiceLine il ON il.InvoiceId = i.InvoiceId\n"
            "WHERE CAST(strftime('%Y', i.InvoiceDate) AS INTEGER) = :year\n"
            "GROUP BY c.CustomerId, customer, c.Country\n"
            "ORDER BY revenue DESC, c.CustomerId\n"
            "LIMIT :n\n"
        ),
    },
    {
        "id": "revenue_by_year",
        "description": "Invoiced revenue and number of invoices for each calendar year",
        "questions": [
            "What is the total revenue per year?",
            "Revenue by year",
            "How much did we sell each year?",
        ],
        "sql": (
            "SELECT CAST(strftime('%Y', InvoiceDate) AS INTEGER) AS year,\n"
            "       ROUND(SUM(Total), 2) AS revenue,\n"
            "       COUNT(*) AS invoices\n"
            "FROM Invoice\n"
            "GROUP BY year\n"
            "ORDER BY year\n"
        ),
    },
    {
        "id": "monthly_revenue",
        "description": "Invoiced revenue and number of invoices for each month of a calendar year",
        "questions": [
            "What was the monthly revenue in {year}?",
            "Revenue by month for {year}",
            "How much did we sell each month in {year}?",
        ],
        "params": {"year": {"type": "int", "min": 2009, "max": 2013, "example": 2013}},
        "sql": (
            "SELECT strftime('%Y-%m', InvoiceDate) AS month,\n"
            "       ROUND(SUM(Total), 2) AS revenue,\n"
            "       COUNT(*) AS invoices\n"
            "FROM Invoice\n"
            "WHERE CAST(strftime('%Y', InvoiceDate) AS INTEGER) = :year\n"
            "GROUP BY month\n"
            "ORDER BY month\n"
        ),
    },
    {
        "id": "sales_by_country",
        "description": "Customers, invoices and invoiced revenue for each billing country, highest revenue first",
        "questions": [
            "What are total sales by country?",
            "Which countries bring in the most revenue?",
            "Revenue by billing country",
        ],
        "sql": (
            "SELECT BillingCountry AS country,\n"
            "       COUNT(DISTINCT CustomerId) AS customers,\n"
            "       COUNT(*) AS invoices,\n"
            "       ROUND(SUM(Total), 2) AS revenue\n"
            "FROM Invoice\n"
            "GROUP BY BillingCountry\n"
            "ORDER BY revenue DESC, country\n"
        ),
    },
    {
        "id": "best_selling_genres",
        "description": "Genres ranked by the number of tracks sold, with their invoiced revenue",
        "questions": [
            "Which genre sells the most tracks?",
            "What are the top {n} genres by tracks sold?",
            "Best-selling genres",
        ],
        "params": {"n": {"type": "int", "default": 5, "min": 1, "max": 25}},
        "sql": (
            "SELECT g.Name AS genre,\n"
            "       SUM(il.Quantity) AS tracks_sold,\n"
            "       ROUND(SUM(il.UnitPrice * il.Quantity), 2) AS revenue\n"
            "FROM InvoiceLine il\n"
            "JOIN Track t ON t.TrackId = il.TrackId\n"
            "JOIN Genre g ON g.GenreId = t.GenreId\n"
            "GROUP BY g.GenreId, g.Name\n"
            "ORDER BY tracks_sold DESC, g.Name\n"
            "LIMIT :n\n"
        ),
    },
    {
        "id": "top_artists_by_tracks_sold",
        "description": "Artists ranked by the number of their tracks sold, with their invoiced revenue",
        "questions": [
            "Who are the top {n} artists by tracks sold?",
            "Which artists sell the most tracks?",
            "Best-selling {n} artists",
        ],
        "params": {"n": {"type": "int", "default": 10, "min": 1, "max": 100}},
        "sql": (
            "SELECT ar.Name AS artist,\n"
            "       SUM(il.Quantity) AS tracks_sold,\n"
            "       ROUND(SUM(il.UnitPrice * il.Quantity), 2) AS revenue\n"
            "FROM InvoiceLine il\n"
            "JOIN Track t   ON t.TrackId = il.TrackId\n"
            "JOIN Album al  ON al.AlbumId = t.AlbumId\n"
            "JOIN Artist ar ON ar.ArtistId = al.ArtistId\n"
            "GROUP BY ar.ArtistId, ar.Name\n"
            "ORDER BY tracks_sold DESC, ar.Name\n"
            "LIMIT :n\n"
        ),
    },
]
