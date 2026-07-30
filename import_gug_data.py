# -*- coding: utf-8 -*-
"""
GUG CRM — one-time historical data import.

Reads the Google-Sheets export "דף עסק GUG - תמחורים_דו״ח הכנסות.csv" (a single
CSV containing several sub-tables laid out side by side) and loads it into
gug_crm.db:

  * punch-card block  (cols 14-20)  -> clients + session packs / subscriptions
  * online sales block (cols 24-35) -> clients + subscriptions + Income rows
  * monthly report Jan-June 2026    -> monthly frontal Income + rent Expense
  * daily training logs Jan-July    -> sessions (one row per logged session)

Safety: the script refuses to run if the database already contains clients,
so re-running it cannot duplicate data. Run with --force to wipe the four
data tables and re-import from scratch.

Usage:
    python import_gug_data.py [--force]
"""

from __future__ import annotations

import re
import sys
import sqlite3
import unicodedata
from datetime import date
from pathlib import Path

import pandas as pd

import database as db

CSV_PATH = Path(__file__).parent / "דף עסק GUG - תמחורים_דו״ח הכנסות.csv"
YEAR = 2026
SNAPSHOT_DATE = date(2026, 7, 17)  # "טעינה אחרונה 17/7" — punch-card state date

# ---------------------------------------------------------------------------
# Name normalization & aliases (log names -> canonical client names)
# ---------------------------------------------------------------------------
ALIASES = {
    "מהרהטה": "מהרטה",
    "יובל אברהמי": "אברהמי",
    "אור פורר": "פורר",
    "חולי": "עוז חולי",
    "גריידי": "דניאל גריידי",
    "הראל שלפר": "הראל שפלר",
    "הראל": "הראל שפלר",
    "מתן טאגניה": "מתן טאגיה",
    "מתן": "מתן טאגיה",
    "שלו אדרי": "שלו עדרי",
    "ניתאי": "ניתאי לוגסי",
    "יואב אשרי": "אשרי",
    "מלול": "עידו מלול",
    "רותם": "רותם סילוק",
    "קרפנקוב": "נדב קרפנקוב",
    "חגג": "אורי חגג",
    "אורי חגג רלף": "אורי חגג",
    "טאגיה": "מתן טאגיה",
    "רותם סילוק ": "רותם סילוק",
    "אמיתי קינן ": "אמיתי קינן",
    "איילון": "איילון הרוש",
    "סידי": "גיא סידי",
    "גיא סידי ": "גיא סידי",
    "אורי גורן": "אורי גורן",
    "אשל לוינטל": "פלג לוינטל",
}

MONTH_NAMES = {
    "ינואר": 1, "פברואר": 2, "מרץ": 3, "אפריל": 4, "מאי": 5, "יוני": 6,
    "יולי": 7, "אוגוסט": 8, "ספטמבר": 9, "אוקטובר": 10, "נובמבר": 11,
    "דצמבר": 12,
}

SESSION_TYPE_WORDS = {
    "אישי", "זוגי", "שלשה", "רביעייה", "חמישייה", "מוביליטי", "אתלטיקה",
    "טסטים", "ים", "חוץ", "חוץ פארק", "פילאטיס",
}

JUNK_NAMES = SESSION_TYPE_WORDS | set(MONTH_NAMES) | {
    "gug", "סה״כ", "סהכ", "סיכום", "שם מתאמן", "שם המתאמן", "חיסכון",
    "בוריס", "אדיר", "קבוצה",
}


def norm(name) -> str:
    """Normalize a Hebrew name cell: unicode NFC, collapse whitespace."""
    if name is None or (isinstance(name, float) and pd.isna(name)):
        return ""
    s = unicodedata.normalize("NFC", str(name))
    s = re.sub(r"\s+", " ", s).strip()
    return ALIASES.get(s, s)


def is_junk_name(s: str) -> bool:
    if not s or len(s) < 2:
        return True
    low = s.lower()
    if low in JUNK_NAMES or s.startswith("רבעון") or s.startswith("טעינה"):
        return True
    if re.fullmatch(r"[\d\s/₪,.+-]+", s):  # numbers / fractions / amounts
        return True
    return False


def parse_money(s):
    """'1,350₪' -> 1350.0 ; returns None if not a clean number."""
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return None
    t = str(s).replace("₪", "").replace(",", "").strip()
    m = re.fullmatch(r"\d+(\.\d+)?", t)
    return float(t) if m else None


def parse_frac(s):
    """'6/10' -> (6, 10) ; returns None otherwise."""
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return None
    m = re.fullmatch(r"(\d+)\s*/\s*(\d+)", str(s).strip())
    return (int(m.group(1)), int(m.group(2))) if m else None


def parse_dm(s, expect_month=None):
    """'23/4' or '08/01' (day/month) -> date in YEAR. Optionally require the
    month to equal expect_month (guards against typos in the log blocks)."""
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return None
    m = re.match(r"^(\d{1,2})\s*/\s*(\d{1,2})", str(s).strip())
    if not m:
        return None
    d, mo = int(m.group(1)), int(m.group(2))
    if expect_month is not None and mo != expect_month:
        return None
    try:
        return date(YEAR, mo, d)
    except ValueError:
        return None


def month_end(month: int) -> date:
    import calendar
    return date(YEAR, month, calendar.monthrange(YEAR, month)[1])


def cell(df, r, c):
    if r >= df.shape[0] or c >= df.shape[1]:
        return None
    v = df.iat[r, c]
    return None if pd.isna(v) else str(v).strip()


# ---------------------------------------------------------------------------
# Import state
# ---------------------------------------------------------------------------
class Importer:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.client_ids: dict[str, int] = {}       # canonical name -> id
        self.client_service: dict[str, str] = {}
        self.has_subscription: set[str] = set()
        self.warnings: list[str] = []
        self.counts = {"clients": 0, "packages": 0, "sessions": 0,
                       "income": 0, "expense": 0}
        self.online_month_totals: dict[int, float] = {}

    def get_client(self, name: str, service: str = "Frontal Training",
                   create: bool = True):
        """Find or create a client by canonical name."""
        if name in self.client_ids:
            return self.client_ids[name]
        if not create:
            return None
        cur = self.conn.execute(
            "INSERT INTO clients (name, phone, service_type, status) "
            "VALUES (?, '', ?, 'Active')",
            (name, service),
        )
        self.client_ids[name] = cur.lastrowid
        self.client_service[name] = service
        self.counts["clients"] += 1
        return cur.lastrowid

    # -- packages ----------------------------------------------------------
    def add_pack(self, name, pack_type, total, remaining, start):
        cid = self.get_client(name)
        self.conn.execute(
            "INSERT INTO packages (client_id, package_type, total_sessions,"
            " remaining_sessions, start_date) VALUES (?, ?, ?, ?, ?)",
            (cid, pack_type, total, remaining, start.isoformat()),
        )
        self.counts["packages"] += 1

    def add_sub(self, name, pack_type, monthly_rate, start, service):
        if name in self.has_subscription:
            return
        cid = self.get_client(name, service)
        self.conn.execute(
            "INSERT INTO packages (client_id, package_type, monthly_rate,"
            " start_date) VALUES (?, ?, ?, ?)",
            (cid, pack_type, monthly_rate, start.isoformat()),
        )
        self.has_subscription.add(name)
        self.counts["packages"] += 1

    # -- finance -----------------------------------------------------------
    def add_income(self, amount, category, when, client_name=None, notes=""):
        cid = self.client_ids.get(client_name) if client_name else None
        self.conn.execute(
            "INSERT INTO financial_transactions (type, amount, category,"
            " payment_method, date, client_id, notes)"
            " VALUES ('Income', ?, ?, 'Other', ?, ?, ?)",
            (amount, category, when.isoformat(), cid, notes),
        )
        self.counts["income"] += 1

    def add_expense(self, amount, category, when, notes=""):
        self.conn.execute(
            "INSERT INTO financial_transactions (type, amount, category,"
            " payment_method, date, client_id, notes)"
            " VALUES ('Expense', ?, ?, 'Other', ?, NULL, ?)",
            (amount, category, when.isoformat(), notes),
        )
        self.counts["expense"] += 1

    # -- sessions ----------------------------------------------------------
    def add_session(self, name, when, note, deducted):
        cid = self.get_client(name)
        self.conn.execute(
            "INSERT INTO sessions (client_id, session_date, notes,"
            " sessions_deducted) VALUES (?, ?, ?, ?)",
            (cid, when.isoformat(), note, 1 if deducted else 0),
        )
        self.counts["sessions"] += 1


# ---------------------------------------------------------------------------
# Block parsers
# ---------------------------------------------------------------------------
def import_punch_block(df, imp: Importer):
    """Punch-card management block: cols 14-20, rows 2-51 (0-indexed).
    Columns: 14 balance, 15 start, 16 debts, 17 used 'x/y', 18 'paid/price',
    19 payment type, 20 name."""
    for r in range(2, 52):
        name = norm(cell(df, r, 20))
        ptype = cell(df, r, 19) or ""
        if is_junk_name(name):
            continue
        if not (ptype or cell(df, r, 17) or cell(df, r, 18)):
            continue  # name with no package info (per-session drop-in)

        imp.get_client(name, "Frontal Training")
        used = parse_frac(cell(df, r, 17))
        paid_price = parse_frac(cell(df, r, 18))
        price = paid_price[1] if paid_price else None
        start = parse_dm(cell(df, r, 15)) or SNAPSHOT_DATE

        if "כרטיסיה" in ptype:
            # Session pack: total from used denominator, else from type name
            m = re.search(r"(\d+)", ptype)
            total = used[1] if used else (int(m.group(1)) if m else None)
            if total is None:
                imp.warnings.append(f"punch row {r+1}: pack size unknown for {name}")
                continue
            remaining = max(total - used[0], 0) if used else total
            imp.add_pack(name, ptype, total, remaining, start)
        elif "ליווי" in ptype or "מנוי" in ptype or "חודשי" in ptype:
            # Monthly subscription / N-month coaching commitment
            months = 3 if "3" in ptype else (2 if "ליווי 2" in ptype else 1)
            rate = round(price / months) if price else None
            if rate:
                imp.add_sub(name, ptype, rate, start, "Frontal Training")
            else:
                imp.warnings.append(f"punch row {r+1}: no rate for {name} ({ptype})")
        # 'פר אימון X' and similar: per-session payer, client only — no package


def import_online_block(df, imp: Importer):
    """Online sales block: cols 24-35, rows 2-43.
    Columns: 28 amount, 29 studio count, 30 end, 31 start, 32 plan,
    33 name, 34 date d/m, 35 month marker / notes."""
    current_month = None
    for r in range(2, 44):
        marker = cell(df, r, 35)
        if marker and marker.strip() in MONTH_NAMES:
            current_month = MONTH_NAMES[marker.strip()]

        name = norm(cell(df, r, 33))
        amount = parse_money(cell(df, r, 28))
        if is_junk_name(name) or amount is None:
            continue

        plan = (cell(df, r, 32) or "").strip()
        when = parse_dm(cell(df, r, 34))
        if when is None:
            when = date(YEAR, current_month, 1) if current_month else SNAPSHOT_DATE
        unpaid = marker is not None and "לא שולם" in marker

        imp.get_client(name, "Online Coaching")

        # Subscription-like plans -> package (once per client)
        if any(k in plan for k in ("ליווי", "מנוי", "חודשי")):
            months = 3 if "3" in plan else (2 if "ליווי 2" in plan else 1)
            rate = round(amount / months) if amount >= 1000 and months > 1 else amount
            start = parse_dm(cell(df, r, 31)) or when
            imp.add_sub(name, plan, rate, start, "Online Coaching")

        if unpaid:
            imp.warnings.append(
                f"online row {r+1}: {name} — {amount:.0f} ILS marked 'לא שולם'"
                " (not paid) — NOT imported as income")
            continue
        note = f"Online sale: {plan}" if plan else "Online sale"
        imp.add_income(amount, "Online Coaching", when, name, note)
        imp.online_month_totals[when.month] = (
            imp.online_month_totals.get(when.month, 0) + amount)


def import_monthly_report(df, imp: Importer):
    """Monthly income report rows 61-66: col 22 net (after rent), col 27 rent,
    col 28 month name. Frontal income = net + rent - online sales that month
    (online sales are imported individually, so subtract to avoid double
    counting)."""
    for r in range(61, 67):
        month_name = (cell(df, r, 28) or "").strip()
        month = MONTH_NAMES.get(month_name)
        if not month:
            continue
        net_raw = cell(df, r, 22) or ""
        net_m = re.match(r"[\d,]+", net_raw.replace("₪", "").strip())
        net = float(net_m.group(0).replace(",", "")) if net_m else None
        rent = parse_money(cell(df, r, 27))
        if net is None:
            imp.warnings.append(f"monthly report: could not parse net for {month_name}")
            continue
        online = imp.online_month_totals.get(month, 0)
        frontal = net + (rent or 0) - online
        imp.add_income(
            round(frontal, 2), "Frontal Training", month_end(month),
            notes=(f"Monthly frontal income (net {net:.0f} + rent {rent or 0:.0f}"
                   f" − online {online:.0f})"))
        if rent:
            imp.add_expense(rent, "Rent / Studio", month_end(month),
                            notes="Studio rent (monthly report)")


# Daily-log blocks: (label, month, row_start, row_end, paid_col, name_col,
#                    date_col, type_col). debt col = paid_col - 1.
LOG_BLOCKS = [
    ("January", 1, 322, 391, 9, 10, 11, 6),
    ("February", 2, 322, 391, 26, 27, 28, 23),
    ("March", 3, 321, 395, 43, 44, 45, 40),
    ("April", 4, 208, 316, 9, 10, 11, 6),
    ("May", 5, 208, 316, 28, 29, 30, 25),
    ("June", 6, 208, 316, 47, 48, 49, 44),
    ("July", 7, 40, 195, 9, 10, 11, 6),
]


def import_daily_logs(df, imp: Importer):
    for label, month, r0, r1, c_paid, c_name, c_date, c_type in LOG_BLOCKS:
        current = None
        n = 0
        for r in range(r0, r1):
            name = norm(cell(df, r, c_name))
            paid = cell(df, r, c_paid)
            debt = cell(df, r, c_paid - 1)

            # Occasionally the name slips one column left (into שולם/חוב)
            if is_junk_name(name):
                for alt in (c_paid, c_paid - 1):
                    altv = norm(cell(df, r, alt))
                    if altv and not is_junk_name(altv):
                        name, paid = altv, cell(df, r, c_name) or paid
                        break

            d = parse_dm(cell(df, r, c_date), expect_month=month)
            if d:
                current = d
            if is_junk_name(name):
                continue
            if current is None:
                current = date(YEAR, month, 1)

            frac = parse_frac(paid)
            parts = []
            if paid:
                parts.append(f"שולם: {paid}" if not frac else f"כרטיסיה: {paid}")
            if debt:
                parts.append(f"חוב: {debt}")
            t = cell(df, r, c_type)
            if t and t in SESSION_TYPE_WORDS:
                parts.append(t)
            imp.add_session(name, current, " | ".join(parts), deducted=bool(frac))
            n += 1
        print(f"  {label}: {n} sessions")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    force = "--force" in sys.argv
    if not CSV_PATH.exists():
        sys.exit(f"CSV not found: {CSV_PATH}")

    db.init_db()
    conn = db.get_connection()
    existing = conn.execute("SELECT COUNT(*) AS n FROM clients").fetchone()["n"]
    if existing and not force:
        sys.exit(f"Database already has {existing} clients — refusing to import "
                 "again (would duplicate data). Run with --force to wipe and "
                 "re-import.")
    if force:
        for t in ("sessions", "financial_transactions", "packages", "clients"):
            conn.execute(f"DELETE FROM {t}")
        print("(--force: cleared existing data)")

    df = pd.read_csv(CSV_PATH, header=None, dtype=str)
    imp = Importer(conn)

    print("Importing punch-card clients & packages...")
    import_punch_block(df, imp)
    print("Importing online sales...")
    import_online_block(df, imp)
    print("Importing monthly income/rent report...")
    import_monthly_report(df, imp)
    print("Importing daily training logs...")
    import_daily_logs(df, imp)

    conn.commit()

    print("\n=== Import complete ===")
    for k, v in imp.counts.items():
        print(f"  {k}: {v}")
    inc = conn.execute("SELECT COALESCE(SUM(amount),0) s FROM"
                       " financial_transactions WHERE type='Income'").fetchone()["s"]
    exp = conn.execute("SELECT COALESCE(SUM(amount),0) s FROM"
                       " financial_transactions WHERE type='Expense'").fetchone()["s"]
    print(f"  total income: {inc:,.0f} ILS | total expenses: {exp:,.0f} ILS")
    if imp.warnings:
        print("\nWarnings:")
        for w in imp.warnings:
            print("  -", w)
    conn.close()


if __name__ == "__main__":
    main()
