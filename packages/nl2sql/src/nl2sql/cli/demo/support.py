"""Support-desk demo dataset: datasource, role table lists and guided questions.

The database is generated, not vendored: ``scripts/generate_demo_datasets.py``
writes ``nl2sql/datasets/support.sqlite`` from a fixed seed, borrowing Chinook's
customer names and email addresses so ``customers.customer_email`` and
``customers.customer_id`` line up with Chinook's ``Customer``. The data is
synthetic and owned by this repository; no third-party dataset is involved.
"""

SUPPORT_DATASOURCE = {
    "id": "support",
    # Read-only, like the other two: see ``chinook.py``.
    "connection": {"type": "sqlite", "database": "data/support.sqlite",
                   "options": {"read_only": True}},
    # Kept deliberately domain-led, and without a sentence about sharing
    # Chinook's customers. Indexing embeds this description, and an earlier
    # draft that named chinook and "customers" out-ranked Chinook itself on
    # Chinook's own questions ("How many invoices were issued in March 2010?").
    # The shared identity lives in the table and column names, which the
    # answerability judge is given, and in the datasets README.
    "description": (
        "Help-desk ticketing system: tickets, the messages exchanged on them, the agents who "
        "handle them, ticket status (open, pending, escalated, resolved, closed), priority, "
        "category, opened and closed timestamps, resolution times and satisfaction scores."
    ),
}

# Every table in the database, and the subsets each demo role may read.
SUPPORT_ALL = ["agents", "customers", "ticket_messages", "tickets"]

SUPPORT_TABLES_BY_ROLE = {
    # Analysts get the whole desk except the agents themselves, mirroring
    # Chinook's analyst, who cannot read Employee.
    "analyst": [t for t in SUPPORT_ALL if t != "agents"],
    # The viewer role reads no support data at all: every table here is either
    # customer data or the text of a customer's messages.
    "viewer": [],
}

SUPPORT_QUESTIONS = [
    "How many support tickets are still open, by priority?",
    "Which support agents resolved the most tickets?",
    "What is the average time to close a ticket, by category?",
    "Which customers raised the most support tickets?",
]
