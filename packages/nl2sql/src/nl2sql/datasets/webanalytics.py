"""Web-analytics demo dataset: datasource, role table lists and guided questions.

The database is generated, not vendored: ``scripts/generate_demo_datasets.py``
writes ``nl2sql/datasets/webanalytics.sqlite`` from a fixed seed. Signed-in
sessions carry Chinook's customer email and id, so traffic can be joined back
to the same people; anonymous sessions leave both null. The data is synthetic
and owned by this repository; no third-party dataset is involved.
"""

from nl2sql.datasets.chinook import DEMO_ROW_LIMIT

WEBANALYTICS_DATASOURCE = {
    "id": "webanalytics",
    "type": "sqlite",
    # Row-limited, like the other two: see ``chinook.py``.
    "connection": {"database": "webanalytics.sqlite"},
    "limits": {"row_limit": DEMO_ROW_LIMIT},
    "description": (
        "Website traffic analytics: visitor sessions with their duration and bounce flag, the "
        "pages viewed in each session and time on page, the referrer domain and marketing channel "
        "(direct, organic, paid, social, email, referral) that brought the visit, the device, "
        "browser and operating system used, and conversion events such as signup, trial started, "
        "newsletter signup and purchase. Signed-in sessions carry the customer_email and "
        "customer_id of the same customers as the chinook music store; anonymous sessions do not."
    ),
}

# What each demo role may read here, as policy patterns.
WEBANALYTICS_ROLES = {
    # Nothing here identifies an employee or an agent, so the analyst reads it all.
    "analyst": {"allow": ["webanalytics"]},
    # Traffic shape without the people: sessions and conversions both carry a
    # customer email, so the viewer sees neither.
    "viewer": {"allow": [f"webanalytics.main.{t}" for t in ("devices", "page_views", "referrers")]},
}

WEBANALYTICS_QUESTIONS = [
    "Which pages were viewed the most last year?",
    "How many sessions came from each marketing channel?",
    "What is the bounce rate by device type?",
    "How many purchases were there per month in 2013?",
]
