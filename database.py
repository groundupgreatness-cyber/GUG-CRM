"""
GUG CRM — SQLite data layer.

All database access goes through this module so the Streamlit UI (app.py)
never touches SQL directly. The database file `gug_crm.db` lives next to
this file.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pandas as pd

DB_PATH = Path(__file__).parent / "gug_crm.db"


# ---------------------------------------------------------------------------
# Connection & schema
# ---------------------------------------------------------------------------
def get_connection() -> sqlite3.Connection:
    """Open a connection with foreign keys enabled and dict-like rows."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    """Create all tables if they don't exist. Safe to call on every start."""
    with get_connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS clients (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                name          TEXT NOT NULL,
                phone         TEXT,
                service_type  TEXT NOT NULL,
                status        TEXT NOT NULL DEFAULT 'Active',
                created_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS packages (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id          INTEGER NOT NULL REFERENCES clients(id),
                package_type       TEXT NOT NULL,
                total_sessions     INTEGER,           -- NULL for monthly subscriptions
                remaining_sessions INTEGER,           -- NULL for monthly subscriptions
                monthly_rate       REAL,              -- NULL for session packs
                start_date         TEXT NOT NULL,
                price              REAL,              -- full price of the package
                amount_paid        REAL NOT NULL DEFAULT 0,
                payment_status     TEXT NOT NULL DEFAULT 'Paid'
                                   CHECK (payment_status IN
                                          ('Paid', 'Partial', 'Pending'))
            );

            CREATE TABLE IF NOT EXISTS sessions (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id         INTEGER NOT NULL REFERENCES clients(id),
                session_date      TEXT NOT NULL,
                session_type      TEXT,
                trainer           TEXT,               -- 'Erez' | 'Yuval'
                package_id        INTEGER REFERENCES packages(id),
                notes             TEXT,
                sessions_deducted INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS financial_transactions (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                type           TEXT NOT NULL CHECK (type IN ('Income', 'Expense')),
                amount         REAL NOT NULL,
                category       TEXT NOT NULL,
                payment_method TEXT,
                date           TEXT NOT NULL,
                client_id      INTEGER REFERENCES clients(id),
                package_id     INTEGER REFERENCES packages(id),
                notes          TEXT
            );

            CREATE TABLE IF NOT EXISTS payout_adjustments (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                month          TEXT NOT NULL,          -- 'YYYY-MM'
                erez_amount    REAL NOT NULL DEFAULT 0,
                yuval_amount   REAL NOT NULL DEFAULT 0,
                savings_amount REAL NOT NULL DEFAULT 0,
                reason         TEXT,
                transaction_id INTEGER REFERENCES financial_transactions(id),
                created_at     TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS weekly_schedule (
                id                       INTEGER PRIMARY KEY AUTOINCREMENT,
                coach_name               TEXT NOT NULL,   -- 'Erez' | 'Yuval'
                day_of_week              TEXT NOT NULL,   -- derived from date, Hebrew label
                date                     TEXT NOT NULL,   -- ISO date of this occurrence
                start_time               TEXT NOT NULL,   -- 'HH:MM'
                end_time                 TEXT NOT NULL,   -- 'HH:MM'
                session_type             TEXT,
                max_capacity             INTEGER NOT NULL DEFAULT 1,
                current_attendees_count  INTEGER NOT NULL DEFAULT 0,
                notes                    TEXT
            );

            -- Who's actually booked into a slot. Kept separate from
            -- weekly_schedule (rather than only a counter) because the admin
            -- needs to see/remove individual attendees, and each booking may
            -- link to the real session/transaction it created — removing a
            -- booking can then cleanly reverse that pack deduction or income.
            CREATE TABLE IF NOT EXISTS schedule_bookings (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                slot_id        INTEGER NOT NULL REFERENCES weekly_schedule(id),
                client_id      INTEGER REFERENCES clients(id),
                dropin_name    TEXT,
                booking_type   TEXT NOT NULL CHECK (booking_type IN
                                                     ('client', 'dropin')),
                session_id     INTEGER REFERENCES sessions(id),
                transaction_id INTEGER REFERENCES financial_transactions(id),
                created_at     TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
            );
            """
        )
        # Migration for databases created before session types existed
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(sessions)")]
        if "session_type" not in cols:
            conn.execute("ALTER TABLE sessions ADD COLUMN session_type TEXT")

        # Migration for databases created before payment tracking existed.
        # Existing packages predate debt tracking, so treat them as fully
        # paid at their known rate.
        pcols = [r["name"] for r in conn.execute("PRAGMA table_info(packages)")]
        if "payment_status" not in pcols:
            conn.execute("ALTER TABLE packages ADD COLUMN price REAL")
            conn.execute("ALTER TABLE packages ADD COLUMN amount_paid REAL"
                         " NOT NULL DEFAULT 0")
            conn.execute("ALTER TABLE packages ADD COLUMN payment_status TEXT"
                         " NOT NULL DEFAULT 'Paid'")
            conn.execute("UPDATE packages SET price = monthly_rate"
                         " WHERE price IS NULL AND monthly_rate IS NOT NULL")
            conn.execute("UPDATE packages SET amount_paid = COALESCE(price, 0)")

        # Migration: optional receipt/reference number on transactions
        tcols = [r["name"] for r in
                 conn.execute("PRAGMA table_info(financial_transactions)")]
        if "reference_number" not in tcols:
            conn.execute("ALTER TABLE financial_transactions"
                         " ADD COLUMN reference_number TEXT")

        # Migration: revenue-split engine needs to know which package each
        # session/transaction belongs to, and who conducted each session.
        # Existing rows get NULL — the payout engine treats those as
        # "pending trainer assignment" rather than guessing, so no historical
        # money is lost or misattributed.
        scols = [r["name"] for r in conn.execute("PRAGMA table_info(sessions)")]
        if "trainer" not in scols:
            conn.execute("ALTER TABLE sessions ADD COLUMN trainer TEXT")
        if "package_id" not in scols:
            conn.execute("ALTER TABLE sessions ADD COLUMN package_id INTEGER"
                         " REFERENCES packages(id)")
        if "package_id" not in tcols:
            conn.execute("ALTER TABLE financial_transactions"
                         " ADD COLUMN package_id INTEGER"
                         " REFERENCES packages(id)")

        # Indexes keep queries fast as history grows
        conn.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_sessions_client
                ON sessions(client_id);
            CREATE INDEX IF NOT EXISTS idx_sessions_date
                ON sessions(session_date);
            CREATE INDEX IF NOT EXISTS idx_packages_client
                ON packages(client_id);
            CREATE INDEX IF NOT EXISTS idx_transactions_date
                ON financial_transactions(date);
            CREATE INDEX IF NOT EXISTS idx_transactions_client
                ON financial_transactions(client_id);
            CREATE INDEX IF NOT EXISTS idx_sessions_package
                ON sessions(package_id);
            CREATE INDEX IF NOT EXISTS idx_transactions_package
                ON financial_transactions(package_id);
            CREATE INDEX IF NOT EXISTS idx_payout_adj_month
                ON payout_adjustments(month);
            CREATE INDEX IF NOT EXISTS idx_schedule_date
                ON weekly_schedule(date);
            CREATE INDEX IF NOT EXISTS idx_bookings_slot
                ON schedule_bookings(slot_id);
            """
        )


# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------
def add_client(name: str, phone: str, service_type: str) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO clients (name, phone, service_type) VALUES (?, ?, ?)",
            (name.strip(), phone.strip(), service_type),
        )
        return cur.lastrowid


def get_clients(status: str | None = None) -> pd.DataFrame:
    query = "SELECT * FROM clients"
    params: tuple = ()
    if status:
        query += " WHERE status = ?"
        params = (status,)
    query += " ORDER BY name COLLATE NOCASE"
    with get_connection() as conn:
        return pd.read_sql_query(query, conn, params=params)


def set_client_status(client_id: int, status: str) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE clients SET status = ? WHERE id = ?", (status, client_id))


# ---------------------------------------------------------------------------
# Packages / subscriptions
# ---------------------------------------------------------------------------
def _resolve_payment(price: float | None, payment_status: str,
                     amount_paid: float | None) -> float:
    """Default amount_paid: full price when 'Paid', zero when 'Pending'."""
    if amount_paid is None:
        return float(price or 0) if payment_status == "Paid" else 0.0
    return float(amount_paid)


def add_session_package(client_id: int, package_type: str, total_sessions: int,
                        start_date: date, price: float | None = None,
                        payment_status: str = "Paid",
                        amount_paid: float | None = None) -> int:
    """Frontal Training: a pack of N sessions."""
    amount_paid = _resolve_payment(price, payment_status, amount_paid)
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO packages
               (client_id, package_type, total_sessions, remaining_sessions,
                start_date, price, amount_paid, payment_status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (client_id, package_type, total_sessions, total_sessions,
             start_date.isoformat(), price, amount_paid, payment_status),
        )
        return cur.lastrowid


def add_subscription(client_id: int, package_type: str, monthly_rate: float,
                     start_date: date, price: float | None = None,
                     payment_status: str = "Paid",
                     amount_paid: float | None = None) -> int:
    """Online Coaching: a monthly subscription (min 3-month commitment).
    `price` defaults to one month's rate — the amount currently owed."""
    if price is None:
        price = monthly_rate
    amount_paid = _resolve_payment(price, payment_status, amount_paid)
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO packages
               (client_id, package_type, monthly_rate, start_date,
                price, amount_paid, payment_status)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (client_id, package_type, monthly_rate, start_date.isoformat(),
             price, amount_paid, payment_status),
        )
        return cur.lastrowid


def add_membership(client_id: int, package_type: str, monthly_rate: float,
                   total_sessions: int, start_date: date,
                   price: float | None = None, payment_status: str = "Paid",
                   amount_paid: float | None = None) -> int:
    """Monthly membership with included sessions (e.g. 8 strength +
    4 mobility/outdoor): stores both the monthly rate and the session
    quota, so logged sessions are deducted from it like a session pack."""
    if price is None:
        price = monthly_rate
    amount_paid = _resolve_payment(price, payment_status, amount_paid)
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO packages
               (client_id, package_type, total_sessions, remaining_sessions,
                monthly_rate, start_date, price, amount_paid, payment_status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (client_id, package_type, total_sessions, total_sessions,
             monthly_rate, start_date.isoformat(), price, amount_paid,
             payment_status),
        )
        return cur.lastrowid


def get_packages(client_id: int | None = None) -> pd.DataFrame:
    query = """
        SELECT p.*, c.name AS client_name
        FROM packages p
        JOIN clients c ON c.id = p.client_id
    """
    params: tuple = ()
    if client_id is not None:
        query += " WHERE p.client_id = ?"
        params = (client_id,)
    query += " ORDER BY p.start_date DESC"
    with get_connection() as conn:
        return pd.read_sql_query(query, conn, params=params)


def get_active_session_package(client_id: int) -> sqlite3.Row | None:
    """Oldest session pack that still has sessions left (FIFO consumption)."""
    with get_connection() as conn:
        return conn.execute(
            """SELECT * FROM packages
               WHERE client_id = ?
                 AND remaining_sessions IS NOT NULL
                 AND remaining_sessions > 0
               ORDER BY start_date ASC, id ASC
               LIMIT 1""",
            (client_id,),
        ).fetchone()


def get_client_stats(client_id: int) -> dict:
    """Quick stats for the Log Session panel: all-time session count, the
    active session pack (if any) and the latest subscription (if any)."""
    with get_connection() as conn:
        total = conn.execute(
            "SELECT COUNT(*) AS n FROM sessions WHERE client_id = ?",
            (client_id,),
        ).fetchone()["n"]
        subscription = conn.execute(
            """SELECT * FROM packages
               WHERE client_id = ? AND monthly_rate IS NOT NULL
               ORDER BY start_date DESC, id DESC LIMIT 1""",
            (client_id,),
        ).fetchone()
    return {
        "total_sessions": total,
        "active_pack": get_active_session_package(client_id),
        "subscription": subscription,
    }


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------
def insert_session(client_id: int, session_date: date,
                   session_type: str | None, trainer: str | None, notes: str,
                   sessions_deducted: int,
                   package_id: int | None = None) -> int:
    """Raw session insert — deduction decisions live in logic.py.
    `package_id` records which package the session deducted from (if any),
    so the payout engine can attribute that package's revenue by trainer."""
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO sessions
               (client_id, session_date, session_type, trainer, package_id,
                notes, sessions_deducted)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (client_id, session_date.isoformat(), session_type, trainer,
             package_id, (notes or "").strip(), sessions_deducted),
        )
        return cur.lastrowid


def decrement_pack(package_id: int) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE packages SET remaining_sessions = remaining_sessions - 1"
            " WHERE id = ?", (package_id,))


def increment_pack(package_id: int) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE packages SET remaining_sessions = remaining_sessions + 1"
            " WHERE id = ?", (package_id,))


def client_has_session_pack(client_id: int) -> bool:
    """Does the client have any session-based pack at all (even exhausted)?"""
    with get_connection() as conn:
        row = conn.execute(
            """SELECT 1 FROM packages
               WHERE client_id = ? AND total_sessions IS NOT NULL LIMIT 1""",
            (client_id,)).fetchone()
    return row is not None


def find_restorable_pack(client_id: int) -> sqlite3.Row | None:
    """Newest pack with at least one consumed session — the inverse of the
    FIFO deduction order, used when a deducted session is deleted."""
    with get_connection() as conn:
        return conn.execute(
            """SELECT * FROM packages
               WHERE client_id = ?
                 AND remaining_sessions IS NOT NULL
                 AND total_sessions IS NOT NULL
                 AND remaining_sessions < total_sessions
               ORDER BY start_date DESC, id DESC
               LIMIT 1""",
            (client_id,)).fetchone()


def fetch_session(session_id: int) -> sqlite3.Row | None:
    with get_connection() as conn:
        return conn.execute("SELECT * FROM sessions WHERE id = ?",
                            (session_id,)).fetchone()


def delete_session_row(session_id: int) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))


def fetch_package(package_id: int) -> sqlite3.Row | None:
    with get_connection() as conn:
        return conn.execute("SELECT * FROM packages WHERE id = ?",
                            (package_id,)).fetchone()


def update_package_payment(package_id: int, amount_paid: float,
                           payment_status: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE packages SET amount_paid = ?, payment_status = ?"
            " WHERE id = ?", (amount_paid, payment_status, package_id))


def table_counts() -> dict:
    with get_connection() as conn:
        return {t: conn.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()["n"]
                for t in ("clients", "packages", "sessions",
                          "financial_transactions", "payout_adjustments",
                          "weekly_schedule", "schedule_bookings")}


# Deletion order matters: children before parents, so foreign-key
# constraints (PRAGMA foreign_keys = ON) never see a dangling reference.
# schedule_bookings -> sessions/financial_transactions/weekly_schedule/clients;
# payout_adjustments -> financial_transactions; sessions -> packages/clients;
# financial_transactions -> packages/clients; packages -> clients.
_RESET_TABLE_ORDER = ("schedule_bookings", "weekly_schedule",
                      "payout_adjustments", "sessions",
                      "financial_transactions", "packages", "clients")


def reset_all_data() -> dict:
    """Wipe every row from every data table and reset AUTOINCREMENT counters
    so new records start at ID 1 again. Schema, indexes and constraints are
    untouched — the database is immediately ready for fresh data.

    Returns {table_name: rows_deleted} for the caller to report back."""
    deleted = {}
    with get_connection() as conn:
        for table in _RESET_TABLE_ORDER:
            n = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
            conn.execute(f"DELETE FROM {table}")
            deleted[table] = n
        placeholders = ", ".join("?" * len(_RESET_TABLE_ORDER))
        conn.execute(
            f"DELETE FROM sqlite_sequence WHERE name IN ({placeholders})",
            _RESET_TABLE_ORDER,
        )
    return deleted


def get_sessions(client_id: int | None = None, limit: int = 100) -> pd.DataFrame:
    query = """
        SELECT s.id, c.name AS client_name, s.session_date, s.session_type,
               s.trainer, s.notes, s.sessions_deducted
        FROM sessions s
        JOIN clients c ON c.id = s.client_id
    """
    params: list = []
    if client_id is not None:
        query += " WHERE s.client_id = ?"
        params.append(client_id)
    query += " ORDER BY s.session_date DESC, s.id DESC LIMIT ?"
    params.append(limit)
    with get_connection() as conn:
        return pd.read_sql_query(query, conn, params=params)


def get_calendar_sessions(client_id: int | None = None,
                          session_type: str | None = None) -> pd.DataFrame:
    """All logged sessions with client name & session type, optionally
    filtered — feeds the calendar view."""
    query = """
        SELECT s.id, s.session_date, s.session_type, s.notes,
               s.sessions_deducted, c.name AS client_name, c.service_type
        FROM sessions s
        JOIN clients c ON c.id = s.client_id
        WHERE 1=1
    """
    params: list = []
    if client_id is not None:
        query += " AND s.client_id = ?"
        params.append(client_id)
    if session_type:
        query += " AND s.session_type = ?"
        params.append(session_type)
    query += " ORDER BY s.session_date"
    with get_connection() as conn:
        return pd.read_sql_query(query, conn, params=params)


def get_session_type_counts(client_id: int, since: str | None = None) -> dict:
    """Completed-session counts per session_type for a client, optionally
    only counting sessions on/after `since` (ISO date) — used for the
    membership quota breakdown."""
    query = ("SELECT session_type, COUNT(*) AS n FROM sessions"
             " WHERE client_id = ?")
    params: list = [client_id]
    if since:
        query += " AND session_date >= ?"
        params.append(since)
    query += " GROUP BY session_type"
    with get_connection() as conn:
        return {row["session_type"]: row["n"]
                for row in conn.execute(query, params)}


# ---------------------------------------------------------------------------
# Finance
# ---------------------------------------------------------------------------
def add_transaction(tx_type: str, amount: float, category: str,
                    payment_method: str, tx_date: date,
                    client_id: int | None = None, notes: str = "",
                    reference: str | None = None,
                    package_id: int | None = None) -> int:
    """`package_id` links this Income transaction to the package/subscription
    it was collected for, so the payout engine can apply the correct revenue
    split (Frontal / Full Online / Hybrid) without guessing from free text."""
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO financial_transactions
               (type, amount, category, payment_method, date, client_id,
                notes, reference_number, package_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (tx_type, amount, category, payment_method, tx_date.isoformat(),
             client_id, notes.strip(), (reference or "").strip() or None,
             package_id),
        )
        return cur.lastrowid


def get_transactions(month: str | None = None, tx_type: str | None = None,
                     category: str | None = None) -> pd.DataFrame:
    """Transaction history. `month` is an ISO prefix like '2026-07';
    `tx_type` is 'Income'/'Expense'; `category` filters exactly."""
    query = """
        SELECT t.id, t.type, t.amount, t.category, t.payment_method, t.date,
               t.reference_number, c.name AS client_name, t.notes
        FROM financial_transactions t
        LEFT JOIN clients c ON c.id = t.client_id
        WHERE 1=1
    """
    params: list = []
    if month:
        query += " AND t.date LIKE ?"
        params.append(f"{month}%")
    if tx_type:
        query += " AND t.type = ?"
        params.append(tx_type)
    if category:
        query += " AND t.category = ?"
        params.append(category)
    query += " ORDER BY t.date DESC, t.id DESC"
    with get_connection() as conn:
        return pd.read_sql_query(query, conn, params=params)


def get_transaction_months() -> list:
    """Distinct 'YYYY-MM' months that have transactions, newest first."""
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT DISTINCT substr(date, 1, 7) AS m
               FROM financial_transactions ORDER BY m DESC""").fetchall()
    return [r["m"] for r in rows]


def get_transaction_categories() -> list:
    """Distinct categories present in the data (for filter dropdowns)."""
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT DISTINCT category FROM financial_transactions
               ORDER BY category""").fetchall()
    return [r["category"] for r in rows if r["category"]]


def fetch_client(client_id: int) -> sqlite3.Row | None:
    with get_connection() as conn:
        return conn.execute("SELECT * FROM clients WHERE id = ?",
                            (client_id,)).fetchone()


def get_finance_summary(month: str | None = None) -> dict:
    """Totals for income, expense and net. Optional 'YYYY-MM' month filter."""
    query = """
        SELECT
            COALESCE(SUM(CASE WHEN type = 'Income' THEN amount END), 0) AS income,
            COALESCE(SUM(CASE WHEN type = 'Expense' THEN amount END), 0) AS expense
        FROM financial_transactions
    """
    params: tuple = ()
    if month:
        query += " WHERE date LIKE ?"
        params = (f"{month}%",)
    with get_connection() as conn:
        row = conn.execute(query, params).fetchone()
    return {
        "income": row["income"],
        "expense": row["expense"],
        "net": row["income"] - row["expense"],
    }


# ---------------------------------------------------------------------------
# Debt tracking
# ---------------------------------------------------------------------------
def get_debts() -> pd.DataFrame:
    """Packages with an outstanding balance (price not fully collected)."""
    query = """
        SELECT p.id, c.name AS client_name, c.phone, p.package_type,
               p.price, p.amount_paid,
               COALESCE(p.price, 0) - COALESCE(p.amount_paid, 0) AS balance,
               p.payment_status, p.start_date
        FROM packages p
        JOIN clients c ON c.id = p.client_id
        WHERE COALESCE(p.price, 0) - COALESCE(p.amount_paid, 0) > 0
           OR p.payment_status != 'Paid'
        ORDER BY balance DESC, p.start_date
    """
    with get_connection() as conn:
        return pd.read_sql_query(query, conn)


def get_total_unpaid() -> float:
    with get_connection() as conn:
        row = conn.execute(
            """SELECT COALESCE(SUM(COALESCE(price, 0) -
                                   COALESCE(amount_paid, 0)), 0) AS s
               FROM packages
               WHERE COALESCE(price, 0) - COALESCE(amount_paid, 0) > 0"""
        ).fetchone()
    return row["s"]


# ---------------------------------------------------------------------------
# Manual overrides (dashboard quick-edit)
# ---------------------------------------------------------------------------
def update_package_fields(package_id: int, total_sessions: int | None,
                          remaining_sessions: int | None,
                          monthly_rate: float | None, start_date: str,
                          price: float | None = None,
                          amount_paid: float | None = None,
                          payment_status: str | None = None) -> None:
    """Manually override a package's counts, rate, dates or payment state."""
    with get_connection() as conn:
        conn.execute(
            """UPDATE packages SET total_sessions = ?, remaining_sessions = ?,
               monthly_rate = ?, start_date = ?, price = ?,
               amount_paid = COALESCE(?, amount_paid),
               payment_status = COALESCE(?, payment_status)
               WHERE id = ?""",
            (total_sessions, remaining_sessions, monthly_rate, start_date,
             price, amount_paid, payment_status, package_id),
        )


def update_transaction_fields(tx_id: int, tx_type: str, amount: float,
                              category: str, payment_method: str | None,
                              tx_date: str, notes: str | None,
                              reference: str | None = None) -> None:
    """Manually override a financial transaction's fields."""
    with get_connection() as conn:
        conn.execute(
            """UPDATE financial_transactions SET type = ?, amount = ?,
               category = ?, payment_method = ?, date = ?, notes = ?,
               reference_number = ?
               WHERE id = ?""",
            (tx_type, amount, category, payment_method, tx_date, notes,
             reference, tx_id),
        )


def delete_transaction(tx_id: int) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM financial_transactions WHERE id = ?", (tx_id,))


# ---------------------------------------------------------------------------
# Chart data (dashboard visualizations)
# ---------------------------------------------------------------------------
def get_session_type_distribution(month: str | None = None) -> pd.DataFrame:
    """Count of logged sessions per session_type, optionally scoped to a
    'YYYY-MM' month — feeds the Session Types donut chart."""
    query = "SELECT session_type, COUNT(*) AS n FROM sessions WHERE 1=1"
    params: list = []
    if month:
        query += " AND session_date LIKE ?"
        params.append(f"{month}%")
    query += " GROUP BY session_type"
    with get_connection() as conn:
        return pd.read_sql_query(query, conn, params=params)


def get_monthly_income_expense_series(n_months: int = 6) -> pd.DataFrame:
    """Income and expense totals for each of the last `n_months` (including
    the current month), oldest first — feeds the Income vs Expense chart."""
    query = """
        SELECT substr(date, 1, 7) AS month,
               COALESCE(SUM(CASE WHEN type='Income' THEN amount END), 0) AS income,
               COALESCE(SUM(CASE WHEN type='Expense' THEN amount END), 0) AS expense
        FROM financial_transactions
        GROUP BY month
        ORDER BY month DESC
        LIMIT ?
    """
    with get_connection() as conn:
        df = pd.read_sql_query(query, conn, params=(n_months,))
    return df.iloc[::-1].reset_index(drop=True)


# ---------------------------------------------------------------------------
# Dashboard helpers
# ---------------------------------------------------------------------------
def get_dashboard_stats() -> dict:
    today = date.today()
    this_month = today.strftime("%Y-%m")
    with get_connection() as conn:
        active_clients = conn.execute(
            "SELECT COUNT(*) AS n FROM clients WHERE status = 'Active'"
        ).fetchone()["n"]
        sessions_this_month = conn.execute(
            "SELECT COUNT(*) AS n FROM sessions WHERE session_date LIKE ?",
            (f"{this_month}%",),
        ).fetchone()["n"]
        low_packs = pd.read_sql_query(
            """SELECT p.id AS package_id, c.name AS client_name, c.phone,
                      p.package_type, p.remaining_sessions
               FROM packages p JOIN clients c ON c.id = p.client_id
               WHERE p.remaining_sessions IS NOT NULL
                 AND p.remaining_sessions <= 2
                 AND c.status = 'Active'
               ORDER BY p.remaining_sessions ASC""",
            conn,
        )
    finance = get_finance_summary(this_month)
    return {
        "active_clients": active_clients,
        "sessions_this_month": sessions_this_month,
        "low_packs": low_packs,
        "finance": finance,
        "month_label": today.strftime("%B %Y"),
        "total_unpaid": get_total_unpaid(),
        "debts": get_debts(),
    }


def get_inactive_clients(days: int) -> pd.DataFrame:
    """Active clients whose most recent logged session is >= `days` days
    old. Clients with NO session history at all are excluded on purpose —
    this flags people who went quiet, not brand-new clients who haven't had
    their first session yet."""
    query = """
        SELECT c.id, c.name, c.phone, c.service_type,
               MAX(s.session_date) AS last_session_date,
               CAST(julianday('now', 'localtime') -
                    julianday(MAX(s.session_date)) AS INTEGER) AS days_inactive
        FROM clients c
        JOIN sessions s ON s.client_id = c.id
        WHERE c.status = 'Active'
        GROUP BY c.id
        HAVING days_inactive >= ?
        ORDER BY days_inactive DESC
    """
    with get_connection() as conn:
        return pd.read_sql_query(query, conn, params=(days,))


# ---------------------------------------------------------------------------
# Revenue Split & Partner Payout Engine
# ---------------------------------------------------------------------------
def get_income_for_payout(month: str | None = None) -> pd.DataFrame:
    """Income transactions for a 'YYYY-MM' month (or all-time if None), with
    the linked package's type and the client's service type (if any) — the
    raw material the payout engine allocates."""
    query = """
        SELECT t.id, t.amount, t.category, t.date, t.client_id, t.package_id,
               c.name AS client_name, c.service_type AS client_service_type,
               p.package_type
        FROM financial_transactions t
        LEFT JOIN clients c ON c.id = t.client_id
        LEFT JOIN packages p ON p.id = t.package_id
        WHERE t.type = 'Income'
    """
    params: tuple = ()
    if month:
        query += " AND t.date LIKE ?"
        params = (f"{month}%",)
    query += " ORDER BY t.date, t.id"
    with get_connection() as conn:
        return pd.read_sql_query(query, conn, params=params)


def get_trainer_session_counts_for_package(package_id: int) -> dict:
    """{'Erez': n, 'Yuval': n} of trainer-tagged sessions that deducted from
    this specific package — the basis for its Frontal revenue split."""
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT trainer, COUNT(*) AS n FROM sessions
               WHERE package_id = ? AND trainer IS NOT NULL
               GROUP BY trainer""", (package_id,)).fetchall()
    return {r["trainer"]: r["n"] for r in rows}


def get_trainer_session_counts_for_client_month(client_id: int,
                                                month: str) -> dict:
    """{'Erez': n, 'Yuval': n} of trainer-tagged sessions logged for a client
    within a month, regardless of package linkage — used for Hybrid clients,
    whose studio sessions aren't tied to a session-quota package."""
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT trainer, COUNT(*) AS n FROM sessions
               WHERE client_id = ? AND trainer IS NOT NULL
                 AND session_date LIKE ?
               GROUP BY trainer""", (client_id, f"{month}%")).fetchall()
    return {r["trainer"]: r["n"] for r in rows}


def add_payout_adjustment(month: str, erez_amount: float, yuval_amount: float,
                          savings_amount: float, reason: str,
                          transaction_id: int | None = None) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO payout_adjustments
               (month, erez_amount, yuval_amount, savings_amount, reason,
                transaction_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (month, erez_amount, yuval_amount, savings_amount, reason,
             transaction_id),
        )
        return cur.lastrowid


def get_payout_adjustments(month: str | None = None) -> pd.DataFrame:
    query = "SELECT * FROM payout_adjustments"
    params: tuple = ()
    if month:
        query += " WHERE month = ?"
        params = (month,)
    query += " ORDER BY created_at DESC"
    with get_connection() as conn:
        return pd.read_sql_query(query, conn, params=params)


def get_adjustment_totals(month: str | None = None) -> dict:
    query = """SELECT COALESCE(SUM(erez_amount), 0) AS erez,
                      COALESCE(SUM(yuval_amount), 0) AS yuval,
                      COALESCE(SUM(savings_amount), 0) AS savings
               FROM payout_adjustments"""
    params: tuple = ()
    if month:
        query += " WHERE month = ?"
        params = (month,)
    with get_connection() as conn:
        row = conn.execute(query, params).fetchone()
    return {"erez": row["erez"], "yuval": row["yuval"],
            "savings": row["savings"]}


def delete_payout_adjustment(adjustment_id: int) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM payout_adjustments WHERE id = ?",
                     (adjustment_id,))


# ---------------------------------------------------------------------------
# Weekly Schedule Management
# ---------------------------------------------------------------------------
def add_schedule_slot(coach_name: str, day_of_week: str, slot_date: date,
                      start_time: str, end_time: str, session_type: str,
                      max_capacity: int, notes: str = "") -> int:
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO weekly_schedule
               (coach_name, day_of_week, date, start_time, end_time,
                session_type, max_capacity, current_attendees_count, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)""",
            (coach_name, day_of_week, slot_date.isoformat(), start_time,
             end_time, session_type, max_capacity, (notes or "").strip()),
        )
        return cur.lastrowid


def update_schedule_slot(slot_id: int, coach_name: str, day_of_week: str,
                         slot_date: date, start_time: str, end_time: str,
                         session_type: str, max_capacity: int,
                         notes: str = "") -> None:
    with get_connection() as conn:
        conn.execute(
            """UPDATE weekly_schedule
               SET coach_name = ?, day_of_week = ?, date = ?,
                   start_time = ?, end_time = ?, session_type = ?,
                   max_capacity = ?, notes = ?
               WHERE id = ?""",
            (coach_name, day_of_week, slot_date.isoformat(), start_time,
             end_time, session_type, max_capacity, (notes or "").strip(),
             slot_id),
        )


def delete_schedule_slot(slot_id: int) -> None:
    """Deletes the slot and its roster (schedule_bookings). Does NOT touch
    any sessions/financial_transactions those bookings had created — those
    stay as permanent history, just no longer linked to a slot."""
    with get_connection() as conn:
        conn.execute("DELETE FROM schedule_bookings WHERE slot_id = ?",
                     (slot_id,))
        conn.execute("DELETE FROM weekly_schedule WHERE id = ?", (slot_id,))


def fetch_schedule_slot(slot_id: int) -> sqlite3.Row | None:
    with get_connection() as conn:
        return conn.execute("SELECT * FROM weekly_schedule WHERE id = ?",
                            (slot_id,)).fetchone()


def get_schedule_slots(from_date: str | None = None,
                       to_date: str | None = None) -> pd.DataFrame:
    """Schedule slots ordered by date/time, optionally bounded by an ISO
    'YYYY-MM-DD' range (either end may be omitted)."""
    query = "SELECT * FROM weekly_schedule WHERE 1=1"
    params: list = []
    if from_date:
        query += " AND date >= ?"
        params.append(from_date)
    if to_date:
        query += " AND date <= ?"
        params.append(to_date)
    query += " ORDER BY date, start_time"
    with get_connection() as conn:
        return pd.read_sql_query(query, conn, params=params)


def increment_slot_attendees(slot_id: int) -> None:
    with get_connection() as conn:
        conn.execute(
            """UPDATE weekly_schedule
               SET current_attendees_count = current_attendees_count + 1
               WHERE id = ?""", (slot_id,))


def decrement_slot_attendees(slot_id: int) -> None:
    with get_connection() as conn:
        conn.execute(
            """UPDATE weekly_schedule
               SET current_attendees_count =
                   MAX(current_attendees_count - 1, 0)
               WHERE id = ?""", (slot_id,))


def add_schedule_booking(slot_id: int, client_id: int | None,
                         dropin_name: str | None, booking_type: str,
                         session_id: int | None = None,
                         transaction_id: int | None = None) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO schedule_bookings
               (slot_id, client_id, dropin_name, booking_type, session_id,
                transaction_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (slot_id, client_id, dropin_name, booking_type, session_id,
             transaction_id),
        )
        return cur.lastrowid


def get_slot_bookings(slot_id: int) -> pd.DataFrame:
    query = """
        SELECT b.id, b.slot_id, b.client_id, b.dropin_name, b.booking_type,
               b.session_id, b.transaction_id, b.created_at,
               c.name AS client_name
        FROM schedule_bookings b
        LEFT JOIN clients c ON c.id = b.client_id
        WHERE b.slot_id = ?
        ORDER BY b.created_at
    """
    with get_connection() as conn:
        return pd.read_sql_query(query, conn, params=(slot_id,))


def fetch_schedule_booking(booking_id: int) -> sqlite3.Row | None:
    with get_connection() as conn:
        return conn.execute("SELECT * FROM schedule_bookings WHERE id = ?",
                            (booking_id,)).fetchone()


def delete_schedule_booking(booking_id: int) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM schedule_bookings WHERE id = ?",
                     (booking_id,))
