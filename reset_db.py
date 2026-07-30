"""
GUG CRM — database reset (command-line).

Deletes ALL records from every data table (clients, packages, sessions,
financial_transactions, payout_adjustments) and resets their AUTOINCREMENT
counters back to 1, while keeping the schema fully intact. The database
file itself is not deleted — it's immediately ready for fresh data.

The same reset logic (database.reset_all_data) also powers the
"Reset Database / איפוס נתונים" button in the app's Settings page, so both
paths behave identically.

Usage:
    python reset_db.py --yes
"""

import sys

import database as db


def main():
    if "--yes" not in sys.argv:
        sys.exit("This will permanently delete ALL data in gug_crm.db.\n"
                 "Run again with --yes to confirm:  python reset_db.py --yes")

    db.init_db()
    deleted = db.reset_all_data()
    for table, n in deleted.items():
        print(f"  {table}: deleted {n} records")
    print("Done — schema intact, all tables empty, IDs reset to start at 1.")


if __name__ == "__main__":
    main()
