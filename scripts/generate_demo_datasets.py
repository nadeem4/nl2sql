"""Generates the synthetic ``support`` and ``webanalytics`` demo databases.

Both are written to ``nl2sql/datasets/`` next to the vendored Chinook
database, and both borrow Chinook's customer identities (name and email) so
that a question spanning two datasources is a real question:

    "which customers with open tickets spent the most?"
    "which customers visited the pricing page but never bought?"

The data is entirely synthetic -- nothing is downloaded, and no third-party
dataset is involved. Only the customer names and email addresses are read
from ``chinook.sqlite`` so the identities line up.

Generation is deterministic: a fixed seed, a fixed customer ordering and an
explicit ``VACUUM`` mean a regeneration produces exactly the same rows, and
byte-identical files on the same SQLite build. Across builds the bytes can
differ (page layout and the header's library version) while the content is
the same, so ``tests/unit/test_demo_datasets.py`` compares the rows against
the committed databases and checks the bytes only between two runs in one
interpreter.

Run it with::

    python scripts/generate_demo_datasets.py
"""

from __future__ import annotations

import argparse
import hashlib
import pathlib
import random
import sqlite3
import sys
from datetime import datetime, timedelta
from typing import List, NamedTuple, Sequence

# The seed. Changing it rewrites both databases, so the checksum test will
# fail until the regenerated files are committed.
SEED = 20090101

# Chinook's invoices run from 2009-01-01 to 2013-12-22; the demo databases
# cover the same window so cross-database questions line up in time.
PERIOD_START = datetime(2009, 1, 1)
PERIOD_END = datetime(2013, 12, 22)
PERIOD_DAYS = (PERIOD_END - PERIOD_START).days

DATASETS_DIR = pathlib.Path(__file__).resolve().parents[1] / "packages" / "nl2sql" / "src" / "nl2sql" / "datasets"


class Customer(NamedTuple):
    """A customer identity borrowed from Chinook."""

    customer_id: int
    first_name: str
    last_name: str
    email: str
    country: str
    city: str


def read_chinook_customers(chinook_path: pathlib.Path) -> List[Customer]:
    """Reads the shared customer identities, ordered by id so the result is stable."""
    con = sqlite3.connect(chinook_path)
    try:
        rows = con.execute(
            "SELECT CustomerId, FirstName, LastName, Email, Country, City "
            "FROM Customer ORDER BY CustomerId"
        ).fetchall()
    finally:
        con.close()
    return [Customer(*row) for row in rows]


def _stamp(rng: random.Random, *, start_day: int = 0, end_day: int = PERIOD_DAYS) -> datetime:
    """A random timestamp inside the Chinook period, to the second."""
    day = rng.randint(start_day, end_day)
    return PERIOD_START + timedelta(days=day, seconds=rng.randint(0, 86399))


def _iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%d %H:%M:%S")


def _write(path: pathlib.Path, schema: str, populate) -> None:
    """Builds a database at ``path`` from scratch, then vacuums it.

    The file is removed first so a rebuild never inherits free pages from the
    previous one, which is what would otherwise make the bytes differ.
    """
    path.unlink(missing_ok=True)
    con = sqlite3.connect(path)
    try:
        con.executescript(schema)
        populate(con)
        con.commit()
        # A fixed page layout, so two runs produce identical bytes.
        con.execute("VACUUM")
        con.commit()
    finally:
        con.close()


# --------------------------------------------------------------------------
# support
# --------------------------------------------------------------------------

SUPPORT_SCHEMA = """
CREATE TABLE agents (
    agent_id      INTEGER PRIMARY KEY,
    full_name     TEXT    NOT NULL,
    email         TEXT    NOT NULL UNIQUE,
    team          TEXT    NOT NULL,
    hired_on      TEXT    NOT NULL
);

CREATE TABLE customers (
    customer_id     INTEGER PRIMARY KEY,
    first_name      TEXT NOT NULL,
    last_name       TEXT NOT NULL,
    customer_email  TEXT NOT NULL UNIQUE,
    country         TEXT,
    city            TEXT,
    signed_up_on    TEXT NOT NULL
);

CREATE TABLE tickets (
    ticket_id       INTEGER PRIMARY KEY,
    customer_id     INTEGER NOT NULL REFERENCES customers(customer_id),
    customer_email  TEXT    NOT NULL,
    agent_id        INTEGER REFERENCES agents(agent_id),
    subject         TEXT    NOT NULL,
    category        TEXT    NOT NULL,
    status          TEXT    NOT NULL,
    priority        TEXT    NOT NULL,
    opened_at       TEXT    NOT NULL,
    closed_at       TEXT,
    satisfaction    INTEGER
);

CREATE TABLE ticket_messages (
    message_id   INTEGER PRIMARY KEY,
    ticket_id    INTEGER NOT NULL REFERENCES tickets(ticket_id),
    sender_role  TEXT    NOT NULL,
    body         TEXT    NOT NULL,
    sent_at      TEXT    NOT NULL
);

CREATE INDEX idx_tickets_customer ON tickets(customer_id);
CREATE INDEX idx_tickets_email    ON tickets(customer_email);
CREATE INDEX idx_tickets_status   ON tickets(status);
CREATE INDEX idx_messages_ticket  ON ticket_messages(ticket_id);
"""

AGENT_NAMES = [
    ("Priya Raman", "Billing"), ("Tomas Novak", "Billing"), ("Aisha Bello", "Technical"),
    ("Marco Rossi", "Technical"), ("Hannah Weber", "Technical"), ("Diego Santos", "Accounts"),
    ("Yuki Tanaka", "Accounts"), ("Elena Petrova", "Billing"), ("Samuel Okoro", "Technical"),
    ("Claire Dubois", "Accounts"),
]

# Weighted so most tickets end up resolved, as a real desk would.
TICKET_STATUSES = (
    ["resolved"] * 9 + ["closed"] * 5 + ["open"] * 3 + ["pending"] * 2 + ["escalated"]
)
TICKET_PRIORITIES = ["low"] * 5 + ["medium"] * 7 + ["high"] * 3 + ["urgent"]
TICKET_CATEGORIES = [
    "Billing", "Playback", "Account access", "Download", "Refund request",
    "Subscription", "Mobile app", "Payment method", "Feature request", "Other",
]

SUBJECTS = {
    "Billing": ["Charged twice for one invoice", "Invoice total looks wrong", "Need a VAT receipt"],
    "Playback": ["Track stops halfway through", "No sound on some albums", "Playback stutters on wifi"],
    "Account access": ["Cannot reset my password", "Locked out after email change", "Two-factor codes rejected"],
    "Download": ["Download fails at 90 percent", "Purchased album will not download", "Downloads are very slow"],
    "Refund request": ["Refund for an accidental purchase", "Wrong album bought", "Duplicate order refund"],
    "Subscription": ["Cancel my subscription", "Upgrade to the family plan", "Subscription renewed unexpectedly"],
    "Mobile app": ["App crashes on startup", "Offline mode not syncing", "Cannot log in on Android"],
    "Payment method": ["Card declined at checkout", "Update my card details", "PayPal option missing"],
    "Feature request": ["Please add a sleep timer", "Allow playlist sharing", "Support for lossless audio"],
    "Other": ["General question about the catalog", "Where do I find my order history", "How do I contact sales"],
}

CUSTOMER_OPENERS = [
    "Hello, I ran into a problem and I would appreciate some help with it.",
    "Hi there, this has happened twice now and I am not sure what to try next.",
    "Good morning, something is not working the way I expected.",
    "Hi, I have been a customer for a while and this is a first for me.",
    "Hello, could someone take a look at this for me please?",
]
AGENT_REPLIES = [
    "Thanks for getting in touch. I have looked at your account and I can see the issue.",
    "Sorry about the trouble. Could you confirm which device you were using?",
    "I have escalated this to the team that owns that area and will update you shortly.",
    "Good news: I have applied a fix on our side, please try again.",
    "I have issued a correction on the account, it should show within one business day.",
]
CUSTOMER_FOLLOWUPS = [
    "That worked, thank you for the quick turnaround.",
    "Still the same behaviour I am afraid.",
    "Understood, I will wait for the update.",
    "Thanks, I can confirm it looks right now.",
    "Appreciate the help, please close the ticket.",
]
AGENT_CLOSERS = [
    "Glad that sorted it. I will close this ticket now, but do reply if it comes back.",
    "Marking this as resolved. Thanks for your patience.",
    "Closing this off, and thank you for the detailed report.",
]


def build_support(con: sqlite3.Connection, customers: Sequence[Customer], rng: random.Random) -> None:
    for agent_id, (name, team) in enumerate(AGENT_NAMES, start=1):
        handle = name.lower().replace(" ", ".")
        hired = PERIOD_START - timedelta(days=rng.randint(30, 900))
        con.execute(
            "INSERT INTO agents VALUES (?,?,?,?,?)",
            (agent_id, name, f"{handle}@support.chinookmusic.example", team, hired.strftime("%Y-%m-%d")),
        )

    for customer in customers:
        signed_up = PERIOD_START - timedelta(days=rng.randint(1, 400))
        con.execute(
            "INSERT INTO customers VALUES (?,?,?,?,?,?,?)",
            (customer.customer_id, customer.first_name, customer.last_name, customer.email,
             customer.country, customer.city, signed_up.strftime("%Y-%m-%d")),
        )

    # Roughly one customer in six never contacts support at all, so questions
    # about "customers with no tickets" have an answer.
    with_tickets = [c for c in customers if rng.random() > 0.17]

    ticket_id = 0
    message_id = 0
    rows: List[tuple] = []
    message_rows: List[tuple] = []

    for customer in with_tickets:
        for _ in range(rng.randint(1, 9)):
            ticket_id += 1
            category = rng.choice(TICKET_CATEGORIES)
            subject = rng.choice(SUBJECTS[category])
            status = rng.choice(TICKET_STATUSES)
            priority = rng.choice(TICKET_PRIORITIES)
            opened = _stamp(rng)
            if status in ("resolved", "closed"):
                closed = opened + timedelta(hours=rng.randint(1, 240), minutes=rng.randint(0, 59))
                satisfaction = rng.choice([3, 4, 4, 5, 5, 5, None])
            else:
                closed = None
                satisfaction = None
            agent_id = rng.randint(1, len(AGENT_NAMES))
            rows.append((ticket_id, customer.customer_id, customer.email, agent_id, subject,
                         category, status, priority, _iso(opened),
                         _iso(closed) if closed else None, satisfaction))

            for role, body, offset in _conversation(rng, opened, closed):
                message_id += 1
                message_rows.append((message_id, ticket_id, role, body, _iso(offset)))

    # Edge case: a ticket that was opened and never got a single message on it.
    ticket_id += 1
    silent_customer = with_tickets[0]
    rows.append((ticket_id, silent_customer.customer_id, silent_customer.email, None,
                 "Opened by mistake, no details given", "Other", "open", "low",
                 _iso(PERIOD_START + timedelta(days=1200, seconds=42)), None, None))

    con.executemany("INSERT INTO tickets VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.executemany("INSERT INTO ticket_messages VALUES (?,?,?,?,?)", message_rows)


def _conversation(rng: random.Random, opened: datetime, closed):
    """The messages on one ticket: an opener, then a few turns."""
    turns = [("customer", rng.choice(CUSTOMER_OPENERS), opened)]
    moment = opened
    for index in range(rng.randint(1, 4)):
        moment = moment + timedelta(hours=rng.randint(1, 30), minutes=rng.randint(0, 59))
        if closed and moment > closed:
            break
        if index % 2 == 0:
            turns.append(("agent", rng.choice(AGENT_REPLIES), moment))
        else:
            turns.append(("customer", rng.choice(CUSTOMER_FOLLOWUPS), moment))
    if closed:
        turns.append(("agent", rng.choice(AGENT_CLOSERS), closed))
    return turns


# --------------------------------------------------------------------------
# webanalytics
# --------------------------------------------------------------------------

WEBANALYTICS_SCHEMA = """
CREATE TABLE devices (
    device_id     INTEGER PRIMARY KEY,
    device_type   TEXT NOT NULL,
    browser       TEXT NOT NULL,
    operating_system TEXT NOT NULL
);

CREATE TABLE referrers (
    referrer_id      INTEGER PRIMARY KEY,
    referrer_domain  TEXT NOT NULL,
    channel          TEXT NOT NULL
);

CREATE TABLE sessions (
    session_id      INTEGER PRIMARY KEY,
    customer_id     INTEGER,
    customer_email  TEXT,
    device_id       INTEGER NOT NULL REFERENCES devices(device_id),
    referrer_id     INTEGER NOT NULL REFERENCES referrers(referrer_id),
    country         TEXT,
    started_at      TEXT NOT NULL,
    ended_at        TEXT NOT NULL,
    duration_seconds INTEGER NOT NULL,
    is_bounce       INTEGER NOT NULL
);

CREATE TABLE page_views (
    page_view_id    INTEGER PRIMARY KEY,
    session_id      INTEGER NOT NULL REFERENCES sessions(session_id),
    page_path       TEXT NOT NULL,
    page_title      TEXT NOT NULL,
    viewed_at       TEXT NOT NULL,
    seconds_on_page INTEGER NOT NULL
);

CREATE TABLE conversions (
    conversion_id   INTEGER PRIMARY KEY,
    session_id      INTEGER NOT NULL REFERENCES sessions(session_id),
    customer_email  TEXT,
    event_type      TEXT NOT NULL,
    occurred_at     TEXT NOT NULL,
    value_usd       REAL
);

CREATE INDEX idx_sessions_customer  ON sessions(customer_id);
CREATE INDEX idx_sessions_email     ON sessions(customer_email);
CREATE INDEX idx_page_views_session ON page_views(session_id);
CREATE INDEX idx_conversions_session ON conversions(session_id);
"""

DEVICES = [
    ("desktop", "Chrome", "Windows"), ("desktop", "Chrome", "macOS"), ("desktop", "Firefox", "Windows"),
    ("desktop", "Firefox", "Linux"), ("desktop", "Safari", "macOS"), ("desktop", "Edge", "Windows"),
    ("mobile", "Safari", "iOS"), ("mobile", "Chrome", "Android"), ("mobile", "Samsung Internet", "Android"),
    ("mobile", "Firefox", "Android"), ("tablet", "Safari", "iPadOS"), ("tablet", "Chrome", "Android"),
]

REFERRERS = [
    ("(direct)", "direct"), ("google.com", "organic"), ("bing.com", "organic"),
    ("duckduckgo.com", "organic"), ("ads.google.com", "paid"), ("facebook.com", "social"),
    ("twitter.com", "social"), ("reddit.com", "social"), ("newsletter.chinookmusic.example", "email"),
    ("musicblog.example", "referral"), ("partner-store.example", "referral"),
]

PAGES = [
    ("/", "Home"),
    ("/pricing", "Pricing"),
    ("/catalog", "Browse the catalog"),
    ("/catalog/rock", "Rock"),
    ("/catalog/jazz", "Jazz"),
    ("/catalog/classical", "Classical"),
    ("/artists", "Artists"),
    ("/albums", "Albums"),
    ("/signup", "Create an account"),
    ("/checkout", "Checkout"),
    ("/support", "Help centre"),
    ("/blog/whats-new", "What's new"),
    ("/account", "Your account"),
]

CONVERSION_TYPES = ["signup"] * 4 + ["trial_started"] * 3 + ["purchase"] * 6 + ["newsletter_signup"] * 2


def build_webanalytics(con: sqlite3.Connection, customers: Sequence[Customer], rng: random.Random) -> None:
    for device_id, (kind, browser, os_name) in enumerate(DEVICES, start=1):
        con.execute("INSERT INTO devices VALUES (?,?,?,?)", (device_id, kind, browser, os_name))
    for referrer_id, (domain, channel) in enumerate(REFERRERS, start=1):
        con.execute("INSERT INTO referrers VALUES (?,?,?)", (referrer_id, domain, channel))

    # About one customer in eight never visits the site, so "customers with no
    # sessions" is a real set.
    visitors = [c for c in customers if rng.random() > 0.12]

    session_id = 0
    page_view_id = 0
    conversion_id = 0
    session_rows: List[tuple] = []
    view_rows: List[tuple] = []
    conversion_rows: List[tuple] = []

    def add_session(customer, started: datetime, pages: Sequence[tuple]) -> int:
        nonlocal session_id, page_view_id
        session_id += 1
        moment = started
        total = 0
        for path, title in pages:
            page_view_id += 1
            seconds = rng.randint(5, 420)
            view_rows.append((page_view_id, session_id, path, title, _iso(moment), seconds))
            moment = moment + timedelta(seconds=seconds + rng.randint(1, 20))
            total += seconds
        ended = started + timedelta(seconds=max(total, 5))
        session_rows.append((
            session_id,
            customer.customer_id if customer else None,
            customer.email if customer else None,
            rng.randint(1, len(DEVICES)),
            rng.randint(1, len(REFERRERS)),
            customer.country if customer else rng.choice(["USA", "Germany", "India", "Brazil", "France"]),
            _iso(started), _iso(ended), int((ended - started).total_seconds()),
            1 if len(pages) == 1 else 0,
        ))
        return session_id

    for customer in visitors:
        for _ in range(rng.randint(1, 12)):
            started = _stamp(rng)
            pages = [rng.choice(PAGES) for _ in range(rng.randint(1, 7))]
            sid = add_session(customer, started, pages)
            # A conversion only ever follows a session that reached checkout
            # or signup, so the funnel holds together.
            if any(path in ("/checkout", "/signup") for path, _ in pages) and rng.random() < 0.45:
                conversion_id += 1
                event = rng.choice(CONVERSION_TYPES)
                value = round(rng.uniform(0.99, 24.99), 2) if event == "purchase" else None
                conversion_rows.append((conversion_id, sid, customer.email, event,
                                        _iso(started + timedelta(minutes=rng.randint(1, 40))), value))

    # Anonymous traffic: sessions with no customer attached at all.
    for _ in range(420):
        started = _stamp(rng)
        pages = [rng.choice(PAGES) for _ in range(rng.randint(1, 4))]
        add_session(None, started, pages)

    # Edge case: a session with exactly one page view, on the pricing page,
    # which never converted.
    add_session(visitors[3], PERIOD_START + timedelta(days=901, seconds=3600), [("/pricing", "Pricing")])

    con.executemany("INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?)", session_rows)
    con.executemany("INSERT INTO page_views VALUES (?,?,?,?,?,?)", view_rows)
    con.executemany("INSERT INTO conversions VALUES (?,?,?,?,?,?)", conversion_rows)


# --------------------------------------------------------------------------


def generate(target_dir: pathlib.Path, chinook_path: pathlib.Path) -> List[pathlib.Path]:
    """Writes ``support.sqlite`` and ``webanalytics.sqlite`` into ``target_dir``."""
    target_dir.mkdir(parents=True, exist_ok=True)
    customers = read_chinook_customers(chinook_path)

    support_path = target_dir / "support.sqlite"
    _write(support_path, SUPPORT_SCHEMA,
           lambda con: build_support(con, customers, random.Random(SEED)))

    web_path = target_dir / "webanalytics.sqlite"
    _write(web_path, WEBANALYTICS_SCHEMA,
           lambda con: build_webanalytics(con, customers, random.Random(SEED + 1)))

    return [support_path, web_path]


def _summarise(path: pathlib.Path) -> str:
    con = sqlite3.connect(path)
    try:
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        counts = ", ".join(
            f"{t}={con.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]}" for t in tables)
    finally:
        con.close()
    size = path.stat().st_size
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    return f"{path.name}: {size / 1024:.0f} KiB  sha256={digest}...  {counts}"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=pathlib.Path, default=DATASETS_DIR,
                        help="Where to write the databases (default: the packaged datasets folder)")
    parser.add_argument("--chinook", type=pathlib.Path, default=None,
                        help="Path to chinook.sqlite (default: the one in the datasets folder)")
    args = parser.parse_args(argv)

    chinook = args.chinook or (DATASETS_DIR / "chinook.sqlite")
    if not chinook.exists():
        print(f"Could not find the Chinook database at {chinook}", file=sys.stderr)
        return 1

    for path in generate(args.target, chinook):
        print(_summarise(path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
