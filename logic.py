"""
GUG CRM — business logic layer.

Sits between the Streamlit UI (app.py) and the data layer (database.py):

    app.py  ──►  logic.py  ──►  database.py  ──►  gug_crm.db

Everything here is plain Python — no Streamlit imports — so it can be unit
tested directly. All user-input problems raise ValidationError with a
human-readable message; app.py catches it and shows a friendly notice.
"""

from __future__ import annotations

import io
import os
import re
import sqlite3
import tempfile
import urllib.parse
from datetime import date, timedelta

import pandas as pd

import config
import database as db

OVERDRAFT_TAG = "חריגה (Overdraft)"


class ValidationError(Exception):
    """Raised when user input fails a business rule. Message is user-facing."""


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------
def require_name(name: str | None) -> str:
    name = (name or "").strip()
    if not name:
        raise ValidationError("Client name is required / חובה להזין שם לקוח.")
    return name


def require_positive(amount, field: str = "Amount") -> float:
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        raise ValidationError(f"{field} must be a number.")
    if amount <= 0:
        raise ValidationError(f"{field} must be greater than 0.")
    return amount


# ---------------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------------
def resolve_collected(status_key: str, entered: float, full_price: float) -> float:
    """Amount actually collected now, per the payment-status convention:
    'Paid' collects the full price, 'Pending' nothing, 'Partial' the
    entered amount (capped at the price, never negative)."""
    if status_key not in config.PAYMENT_STATUSES:
        raise ValidationError(f"Unknown payment status: {status_key}")
    if status_key == "Paid":
        return float(full_price)
    if status_key == "Pending":
        return 0.0
    return min(max(float(entered or 0), 0.0), float(full_price))


# ---------------------------------------------------------------------------
# Clients & package assignment
# ---------------------------------------------------------------------------
def create_client(name: str, phone: str, service_type: str) -> int:
    return db.add_client(require_name(name), phone or "", service_type)


def assign_package(client_id: int, kind: str, item: dict, start: date,
                   status_key: str, entered_amount: float, pay_method: str,
                   record_income: bool = True) -> str:
    """Assign a pack/membership/subscription with payment tracking and an
    optional income record. `kind` is 'pack' | 'member' | 'sub'.
    Returns a human-readable summary message."""
    if client_id is None:
        raise ValidationError("Select a client first.")
    full_price = item["price"] if kind == "pack" else item["monthly_rate"]
    collected = resolve_collected(status_key, entered_amount, full_price)

    if kind == "pack":
        package_id = db.add_session_package(
            client_id, item["label"], item["sessions"], start,
            price=full_price, payment_status=status_key,
            amount_paid=collected)
        category = config.CATEGORY_FRONTAL
        msg = f"{item['label']} assigned."
    elif kind == "member":
        package_id = db.add_membership(
            client_id, item["label"], item["monthly_rate"], item["sessions"],
            start, payment_status=status_key, amount_paid=collected)
        category = config.CATEGORY_FRONTAL
        msg = (f"{item['label']} assigned "
               f"({item['sessions']} sessions included).")
    elif kind == "sub":
        package_id = db.add_subscription(
            client_id, item["label"], item["monthly_rate"], start,
            payment_status=status_key, amount_paid=collected)
        category = config.CATEGORY_ONLINE
        msg = f"{item['label']} subscription assigned."
    else:
        raise ValidationError(f"Unknown package kind: {kind}")

    if record_income and collected > 0:
        db.add_transaction("Income", collected, category, pay_method, start,
                           client_id, notes=f"Assigned: {item['label']}",
                           package_id=package_id)
        msg += f" Income of ₪{collected:,.0f} recorded."
    if collected < full_price:
        msg += f" Open balance: ₪{full_price - collected:,.0f}."
    return msg


# ---------------------------------------------------------------------------
# Sessions (balance calculation, deduction, overdraft, restore)
# ---------------------------------------------------------------------------
def log_session(client_id: int, session_date: date, notes: str = "",
                deduct: bool = True, session_type: str | None = None,
                trainer: str | None = None) -> dict:
    """
    Log a completed session. Deduction is FIFO: the oldest pack with
    remaining sessions loses 1. If deduction was requested but the client's
    packs are exhausted, the session is still logged and flagged as an
    overdraft (חריגה) instead of failing.

    `trainer` ('Erez'/'Yuval') records who conducted the session — required,
    since it drives the Frontal Training revenue split (70/10/20). The
    package deducted from (if any) is recorded on the session too, so the
    payout engine can attribute that package's revenue by trainer.

    Returns {"deducted": bool, "remaining": int|None, "overdraft": bool,
             "session_id": int}
    """
    if client_id is None:
        raise ValidationError("Select a client first.")
    if trainer not in config.TRAINERS:
        raise ValidationError("Select a trainer / בחר/י מאמן.")
    deducted, remaining, overdraft, package_id = False, None, False, None

    if deduct:
        pack = db.get_active_session_package(client_id)
        if pack:
            db.decrement_pack(pack["id"])
            deducted = True
            remaining = pack["remaining_sessions"] - 1
            package_id = pack["id"]
        elif db.client_has_session_pack(client_id):
            # Packs exist but none has sessions left — log with a clear flag
            overdraft = True
            notes = f"{OVERDRAFT_TAG} | {notes}".strip(" |")

    session_id = db.insert_session(client_id, session_date, session_type,
                                   trainer, notes, 1 if deducted else 0,
                                   package_id)
    return {"deducted": deducted, "remaining": remaining,
            "overdraft": overdraft, "session_id": session_id}


def delete_session(session_id: int) -> dict:
    """
    Delete a logged session. If it had deducted from a pack, restore (+1)
    to the pack the deduction came from: deduction is FIFO, so the last
    deduction landed on the newest pack with at least one consumed session.
    """
    sess = db.fetch_session(session_id)
    if sess is None:
        return {"deleted": False, "restored": False, "remaining": None,
                "package_type": None}

    restored, remaining, package_type = False, None, None
    if sess["sessions_deducted"]:
        pack = db.find_restorable_pack(sess["client_id"])
        if pack:
            db.increment_pack(pack["id"])
            restored = True
            remaining = pack["remaining_sessions"] + 1
            package_type = pack["package_type"]

    db.delete_session_row(session_id)
    return {"deleted": True, "restored": restored, "remaining": remaining,
            "package_type": package_type}


# ---------------------------------------------------------------------------
# Debt collection (package payment-status transitions)
# ---------------------------------------------------------------------------
def record_debt_payment(package_id: int, amount: float,
                        pay_method: str = "Other",
                        pay_date: date | None = None,
                        record_income: bool = True) -> dict:
    """Apply a collected payment: raise amount_paid, refresh the status
    ('Paid' once the price is covered), optionally record the income."""
    amount = require_positive(amount, "Payment amount")
    pack = db.fetch_package(package_id)
    if pack is None:
        raise ValidationError("Package not found.")
    price = pack["price"] or 0
    new_paid = (pack["amount_paid"] or 0) + amount
    status = "Paid" if new_paid >= price else "Partial"
    db.update_package_payment(package_id, new_paid, status)
    if record_income:
        # Categorize the collected debt under the client's service line
        client = db.fetch_client(pack["client_id"])
        category = (config.CATEGORY_ONLINE
                    if client and client["service_type"] == config.SERVICE_ONLINE
                    else config.CATEGORY_FRONTAL)
        db.add_transaction("Income", amount, category, pay_method,
                           pay_date or date.today(), pack["client_id"],
                           notes=f"גביית חוב: {pack['package_type']}",
                           package_id=package_id)
    return {"ok": True, "client_id": pack["client_id"],
            "package_type": pack["package_type"], "amount_paid": new_paid,
            "balance": max(price - new_paid, 0), "status": status}


# ---------------------------------------------------------------------------
# Revenue Split & Partner Payout Engine
# ---------------------------------------------------------------------------
# Every logged session records its trainer and (if it deducted from a paid
# package) that package's id. Payouts are computed on read from the Income
# transactions of a month — nothing is pre-materialized, so a payout always
# reflects the latest session/trainer data, including sessions logged after
# the money was originally collected.
def _is_hybrid_package(package_type: str | None) -> bool:
    return bool(package_type) and config.HYBRID_LABEL_MARKER in package_type


def _revenue_bucket(category: str, client_service_type: str | None) -> str | None:
    """Classify an Income transaction's category as 'frontal' / 'online' /
    None (unclassified) — recognizes both the current Hebrew categories and
    pre-standardization labels used by earlier versions of this app. An
    ambiguous legacy category (e.g. the old 'Debt Collection' label) falls
    back to the linked client's service type, the same convention
    record_debt_payment() uses when recording new debt collections."""
    if category == config.CATEGORY_FRONTAL or category in config.LEGACY_FRONTAL_CATEGORIES:
        return "frontal"
    if category == config.CATEGORY_ONLINE or category in config.LEGACY_ONLINE_CATEGORIES:
        return "online"
    if client_service_type == config.SERVICE_FRONTAL:
        return "frontal"
    if client_service_type == config.SERVICE_ONLINE:
        return "online"
    return None


def _split_frontal_by_counts(gross: float, counts: dict) -> dict:
    """Split `gross` ILS using the Frontal rule (70% conducting trainer /
    10% partner / 20% savings), proportionally across however many
    trainer-tagged sessions were logged. Savings is always exactly 20% of
    gross, independent of the trainer split. If no trainer-tagged sessions
    exist yet, the conducting/partner 80% is held as 'pending' rather than
    guessed — only savings is allocated immediately."""
    n_erez = counts.get("Erez", 0)
    n_yuval = counts.get("Yuval", 0)
    n_total = n_erez + n_yuval
    savings = gross * config.SAVINGS_PCT
    if n_total == 0:
        return {"erez": 0.0, "yuval": 0.0, "savings": savings,
                "pending": gross - savings, "status": "pending_trainer",
                "note": "No trainer-tagged sessions logged yet"}
    per_session = gross / n_total
    erez = per_session * (n_erez * config.FRONTAL_CONDUCTING_PCT
                          + n_yuval * config.FRONTAL_PARTNER_PCT)
    yuval = per_session * (n_yuval * config.FRONTAL_CONDUCTING_PCT
                           + n_erez * config.FRONTAL_PARTNER_PCT)
    return {"erez": erez, "yuval": yuval, "savings": savings, "pending": 0.0,
            "status": "allocated",
            "note": f"{n_erez} session(s) Erez, {n_yuval} session(s) Yuval"}


def _split_online(gross: float) -> dict:
    """Full Online Coaching rule: 40% Erez / 40% Yuval / 20% savings."""
    return {"erez": gross * config.ONLINE_EREZ_PCT,
            "yuval": gross * config.ONLINE_YUVAL_PCT,
            "savings": gross * config.SAVINGS_PCT, "pending": 0.0,
            "status": "allocated", "note": "Full Online 40/40/20"}


def _split_hybrid(client_id: int, month: str, gross: float) -> dict:
    """Hybrid Online + Studio rule: a configured share of the fee is treated
    as frontal studio revenue (Frontal rule, trainer-attributed via sessions
    logged for this client this month — Hybrid subscriptions carry no
    session quota, so sessions can't be linked by package_id); the remainder
    is online revenue (40/40/20)."""
    frontal_amt = gross * config.HYBRID_FRONTAL_SHARE
    online_amt = gross - frontal_amt
    counts = db.get_trainer_session_counts_for_client_month(client_id, month)
    f = _split_frontal_by_counts(frontal_amt, counts)
    o = _split_online(online_amt)
    return {
        "erez": f["erez"] + o["erez"], "yuval": f["yuval"] + o["yuval"],
        "savings": f["savings"] + o["savings"], "pending": f["pending"],
        "status": f["status"],
        "note": (f"Frontal share ₪{frontal_amt:,.0f} ({f['note']}) + "
                f"Online share ₪{online_amt:,.0f}"),
    }


def compute_monthly_payouts(month: str) -> dict:
    """
    Allocate every Income transaction in `month` ('YYYY-MM') between Coach
    Erez, Coach Yuval and the Business Savings Fund (חיסכון העסק), plus any
    manual adjustments recorded for the month.

    Returns {"month", "erez_total", "yuval_total", "savings_total",
             "pending_total", "rows": [...]} — `rows` is the detailed,
    per-transaction (and per-adjustment) breakdown for the UI table.
    """
    income = db.get_income_for_payout(month)
    rows = []
    erez_total = yuval_total = savings_total = pending_total = 0.0

    for _, tx in income.iterrows():
        gross = float(tx["amount"])
        category = tx["category"]
        package_type = tx["package_type"]
        bucket = _revenue_bucket(category, tx["client_service_type"])

        if bucket == "frontal":
            if pd.notna(tx["package_id"]):
                counts = db.get_trainer_session_counts_for_package(
                    int(tx["package_id"]))
                split = _split_frontal_by_counts(gross, counts)
            else:
                split = {"erez": 0.0, "yuval": 0.0,
                         "savings": gross * config.SAVINGS_PCT,
                         "pending": gross * (1 - config.SAVINGS_PCT),
                         "status": "pending_trainer",
                         "note": "No package link (legacy entry) — needs "
                                 "manual reconciliation"}
            rule = "Frontal 70/10/20"
        elif bucket == "online":
            if (pd.notna(tx["package_id"]) and pd.notna(tx["client_id"])
                    and _is_hybrid_package(package_type)):
                split = _split_hybrid(int(tx["client_id"]), month, gross)
                rule = "Hybrid (frontal-share 70/10/20 + online 40/40/20)"
            else:
                split = _split_online(gross)
                rule = "Full Online 40/40/20"
        else:
            split = {"erez": 0.0, "yuval": 0.0, "savings": 0.0,
                     "pending": 0.0, "status": "unclassified",
                     "note": "Not part of the revenue-split categories"}
            rule = "Not auto-allocated"

        erez_total += split["erez"]
        yuval_total += split["yuval"]
        savings_total += split["savings"]
        pending_total += split["pending"]
        rows.append({
            "date": tx["date"], "client_name": tx["client_name"] or "—",
            "description": package_type or category, "category": category,
            "rule": rule, "note": split["note"], "gross": gross,
            "erez": split["erez"], "yuval": split["yuval"],
            "savings": split["savings"], "status": split["status"],
        })

    for _, adj in db.get_payout_adjustments(month).iterrows():
        erez_total += adj["erez_amount"]
        yuval_total += adj["yuval_amount"]
        savings_total += adj["savings_amount"]
        rows.append({
            "date": adj["created_at"][:10], "client_name": "—",
            "description": f"Manual adjustment: {adj['reason'] or ''}",
            "category": "Manual", "rule": "Manual override", "note": "",
            "gross": (adj["erez_amount"] + adj["yuval_amount"]
                     + adj["savings_amount"]),
            "erez": adj["erez_amount"], "yuval": adj["yuval_amount"],
            "savings": adj["savings_amount"], "status": "manual",
        })

    return {"month": month, "erez_total": erez_total,
            "yuval_total": yuval_total, "savings_total": savings_total,
            "pending_total": pending_total, "rows": rows}


def alltime_accumulated_savings() -> float:
    """Total Business Savings Fund accrued across all history. Both revenue
    rules allocate exactly 20% to savings regardless of trainer split or
    Hybrid/Full distinction, so this only needs classification (not the full
    per-session trainer split) — but it reuses the same _revenue_bucket()
    classifier as compute_monthly_payouts() so the two always agree,
    including on legacy pre-standardization category labels."""
    income = db.get_income_for_payout(None)
    savings = sum(
        float(tx["amount"]) * config.SAVINGS_PCT
        for _, tx in income.iterrows()
        if _revenue_bucket(tx["category"], tx["client_service_type"])
    )
    adjustments = db.get_adjustment_totals(None)
    return savings + adjustments["savings"]


def add_payout_adjustment(month: str, erez_amount: float, yuval_amount: float,
                          savings_amount: float, reason: str,
                          transaction_id: int | None = None) -> int:
    """Manually record or correct a payout amount for a month — additive
    (never silently overwrites computed numbers), so it always shows up as
    its own auditable line in the detail list. Amounts may be negative to
    correct/reduce a prior allocation."""
    if not month:
        raise ValidationError("Month is required for a payout adjustment.")
    if erez_amount == 0 and yuval_amount == 0 and savings_amount == 0:
        raise ValidationError("Enter at least one non-zero amount to adjust.")
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("A reason is required for manual adjustments "
                              "(kept for audit purposes).")
    return db.add_payout_adjustment(month, erez_amount, yuval_amount,
                                    savings_amount, reason, transaction_id)


# ---------------------------------------------------------------------------
# Stats & financial rollups (thin delegates so the UI never touches SQL)
# ---------------------------------------------------------------------------
def client_stats(client_id: int) -> dict:
    return db.get_client_stats(client_id)


def session_type_counts(client_id: int, since: str | None = None) -> dict:
    return db.get_session_type_counts(client_id, since)


def dashboard_stats() -> dict:
    return db.get_dashboard_stats()


def finance_summary(month: str | None = None) -> dict:
    return db.get_finance_summary(month)


def pnl(month: str | None = None) -> dict:
    """Profit & Loss rollup for a month ('YYYY-MM') or all time (None):
    total income, total expenses, net profit and profit margin %."""
    s = db.get_finance_summary(month)
    income, expense = s["income"], s["expense"]
    net = income - expense
    margin = (net / income * 100) if income else None
    return {"income": income, "expense": expense, "net": net,
            "margin": margin}


def transaction_months() -> list:
    """Months available for P&L / history filtering — every month that has
    data, plus the current month, newest first."""
    months = db.get_transaction_months()
    current = date.today().strftime("%Y-%m")
    if current not in months:
        months.insert(0, current)
    return months


def session_type_distribution(month: str | None = None) -> dict:
    """{'Strength': n, 'Mobility': n, 'Athletics': n} for the given month
    ('YYYY-MM') or all-time — feeds the dashboard donut chart. Sessions with
    no recognized type (pre-migration legacy rows) are excluded."""
    df = db.get_session_type_distribution(month)
    return {row["session_type"]: int(row["n"]) for _, row in df.iterrows()
            if row["session_type"] in config.SESSION_TYPES}


def monthly_income_expense_series(n_months: int = 6) -> pd.DataFrame:
    return db.get_monthly_income_expense_series(n_months)


def transaction_categories() -> list:
    """Every category selectable in filters: the standardized lists plus
    anything already present in historical data."""
    known = list(dict.fromkeys(
        config.INCOME_CATEGORIES + config.EXPENSE_CATEGORIES))
    extra = [c for c in db.get_transaction_categories() if c not in known]
    return known + sorted(extra)


# ---------------------------------------------------------------------------
# WhatsApp Quick-Action Messaging & Inactivity Detection
# ---------------------------------------------------------------------------
def format_phone_intl(phone: str | None) -> str | None:
    """Convert an Israeli phone number in any common local format (leading
    0, dashes, spaces, parens, or already-international) to the digits-only
    international form WhatsApp's click-to-chat links expect — e.g.
    '050-458-4404' or '0584584404' -> '972584584404'.

    Returns None when the input is empty or doesn't look like a real
    Israeli number, so callers can hide the WhatsApp button instead of
    linking to a dead/malformed chat."""
    if not phone:
        return None
    cleaned = re.sub(r"[^\d+]", "", str(phone))
    if cleaned.startswith("+"):
        cleaned = cleaned[1:]
    if cleaned.startswith("972"):
        digits = cleaned
    elif cleaned.startswith("0"):
        digits = "972" + cleaned[1:]
    else:
        return None  # not a recognizable local (0...) or international number
    if not digits.isdigit() or not (10 <= len(digits) <= 13):
        return None
    return digits


def whatsapp_message(template_key: str, **kwargs) -> str:
    """Render one of the WhatsApp quick-message templates (config.py) with
    the given placeholders (e.g. name=, session_type=, remaining=)."""
    template = config.WHATSAPP_TEMPLATES.get(template_key)
    if template is None:
        raise ValidationError(f"Unknown WhatsApp template: {template_key}")
    try:
        return template.format(**kwargs)
    except KeyError as e:
        raise ValidationError(
            f"WhatsApp template '{template_key}' is missing a value for {e}.")


def whatsapp_link(phone: str | None, template_key: str, **kwargs) -> str | None:
    """Build a wa.me click-to-chat link pre-filled with a Hebrew message
    template.

    `phone` must be the RECIPIENT CLIENT's own number — that's the only
    number this function ever puts in the URL. The returned link is always
    exactly `https://wa.me/<recipient-intl-digits>?text=<encoded-message>`:
    one path segment, one query parameter. There is no 'from'/'sender'
    parameter in wa.me's URL scheme at all, so none is ever added here —
    the message is sent from whichever WhatsApp account is logged into the
    device that opens the link, which this URL has no way to influence.

    Returns None when the phone number is missing/invalid so callers can
    gracefully hide the button rather than link to a broken chat."""
    intl_phone = format_phone_intl(phone)
    if not intl_phone:
        return None
    message = whatsapp_message(template_key, **kwargs)
    return f"https://wa.me/{intl_phone}?text={urllib.parse.quote(message)}"


def detect_inactive_clients(
        days: int = config.INACTIVITY_THRESHOLD_DAYS) -> pd.DataFrame:
    """Active clients who haven't had a logged session in `days` days —
    the Dashboard's Inactivity / מתאמנים שנעלמו list."""
    return db.get_inactive_clients(days)


def booking_whatsapp_link(session_type: str, day_of_week: str,
                          slot_date: str, start_time: str) -> str:
    """Build the client-facing 'request to book a slot' WhatsApp link.

    This is the ONE deliberate exception to whatsapp_link()'s recipient-only
    rule: here Coach Erez really IS the recipient (a prospective client is
    messaging him to ask for a spot), not a client being contacted by the
    business. Kept as its own function, targeting the fixed
    config.COACH_BOOKING_PHONE_INTL, precisely so this direction can never
    be confused with — or accidentally merged into — whatsapp_link(), whose
    entire contract is that only a client's number ever appears in its URL."""
    label = config.SESSION_TYPES.get(session_type, session_type)
    message = whatsapp_message("booking_request", session_type=label,
                               day_of_week=day_of_week, date=slot_date,
                               start_time=start_time)
    return (f"https://wa.me/{config.COACH_BOOKING_PHONE_INTL}"
           f"?text={urllib.parse.quote(message)}")


def renewal_whatsapp_link() -> str:
    """Fallback link for the public schedule's self-identification flow
    (Step 1): shown when a visitor isn't a recognized active client with
    remaining sessions. Same coach-as-recipient exception as
    booking_whatsapp_link() above, kept as its own function for the same
    reason — this direction must never be confused with whatsapp_link()."""
    message = whatsapp_message("renew_or_register")
    return (f"https://wa.me/{config.COACH_BOOKING_PHONE_INTL}"
           f"?text={urllib.parse.quote(message)}")


# ---------------------------------------------------------------------------
# Weekly Schedule Management
# ---------------------------------------------------------------------------
def derive_day_of_week(slot_date: date) -> str:
    return config.HEBREW_DAY_NAMES[slot_date.weekday()]


def _validate_slot_inputs(coach_name: str, start_time: str, end_time: str,
                          session_type: str, max_capacity: int) -> None:
    if coach_name not in config.TRAINERS:
        raise ValidationError("Select a coach / בחר/י מאמן.")
    if session_type not in config.SESSION_TYPES:
        raise ValidationError("Select a session type / בחר/י סוג אימון.")
    if not start_time or not end_time:
        raise ValidationError("Start and end time are required.")
    if end_time <= start_time:
        raise ValidationError("End time must be after start time.")
    if not isinstance(max_capacity, int) or max_capacity < 1:
        raise ValidationError("Capacity must be at least 1.")


def create_schedule_slot(coach_name: str, slot_date: date, start_time: str,
                         end_time: str, session_type: str, max_capacity: int,
                         notes: str = "") -> int:
    _validate_slot_inputs(coach_name, start_time, end_time, session_type,
                          max_capacity)
    day_of_week = derive_day_of_week(slot_date)
    return db.add_schedule_slot(coach_name, day_of_week, slot_date,
                                start_time, end_time, session_type,
                                max_capacity, notes)


def update_schedule_slot(slot_id: int, coach_name: str, slot_date: date,
                         start_time: str, end_time: str, session_type: str,
                         max_capacity: int, notes: str = "") -> None:
    _validate_slot_inputs(coach_name, start_time, end_time, session_type,
                          max_capacity)
    slot = db.fetch_schedule_slot(slot_id)
    if slot is None:
        raise ValidationError("Schedule slot not found.")
    if max_capacity < slot["current_attendees_count"]:
        raise ValidationError(
            f"Capacity can't be set below the {slot['current_attendees_count']} "
            "people already booked into this slot.")
    day_of_week = derive_day_of_week(slot_date)
    db.update_schedule_slot(slot_id, coach_name, day_of_week, slot_date,
                            start_time, end_time, session_type,
                            max_capacity, notes)


def delete_schedule_slot(slot_id: int) -> None:
    db.delete_schedule_slot(slot_id)


def _with_available_seats(slots: pd.DataFrame) -> pd.DataFrame:
    if slots.empty:
        slots = slots.copy()
        slots["available_seats"] = pd.Series(dtype=int)
        return slots
    slots = slots.copy()
    slots["available_seats"] = (
        slots["max_capacity"] - slots["current_attendees_count"]
    ).clip(lower=0)
    return slots


def list_schedule_slots(from_date: str | None = None,
                        to_date: str | None = None) -> pd.DataFrame:
    """All slots in an optional date range, with an `available_seats`
    column computed for convenience. Used by the admin management view."""
    return _with_available_seats(db.get_schedule_slots(from_date, to_date))


def upcoming_public_slots(
        days_ahead: int = config.SCHEDULE_LOOKAHEAD_DAYS) -> pd.DataFrame:
    """Slots from today through `days_ahead` days out — feeds the public
    client-facing schedule view."""
    today = date.today()
    end = today + timedelta(days=days_ahead)
    return list_schedule_slots(today.isoformat(), end.isoformat())


def slots_by_date(slots: pd.DataFrame, days: list[date]) -> dict[str, pd.DataFrame]:
    """Group already-fetched slots into a fixed set of calendar days (some
    possibly with none) for the weekly grid layouts — the public and admin
    views share this so the two grids can never disagree on which days/
    ordering appear, or how a day with no slots is represented."""
    result = {}
    for d in days:
        iso = d.isoformat()
        day_slots = (slots[slots["date"] == iso] if not slots.empty
                    else slots).sort_values("start_time")
        result[iso] = day_slots
    return result


def find_client_by_phone(phone: str) -> dict | None:
    """Public self-identification lookup for the schedule view: matches an
    entered phone number against ACTIVE clients only, comparing normalized
    international digits via format_phone_intl() so any common local format
    the visitor types in matches however the number was originally entered
    by the admin.

    Recognizes BOTH session packs (כרטיסיה, counted) and monthly
    subscriptions (מנוי/ליווי, uncounted) as a bookable "active package" —
    a subscription client has remaining_sessions=0 but is still eligible to
    self-book (can_book=True), since subscriptions aren't decremented per
    visit in this schema. Also returns the client's own upcoming bookings,
    for the personal status dashboard and the "you're registered for this
    one" grid highlight.

    Returns ONLY that one client's own data — never anything about any
    other client — or None on no match, so the public view can never leak
    the client list."""
    intl = format_phone_intl(phone)
    if not intl:
        return None
    clients = db.get_clients(status="Active")
    for _, c in clients.iterrows():
        if format_phone_intl(c["phone"]) == intl:
            client_id = int(c["id"])
            stats = db.get_client_stats(client_id)
            pack, sub = stats["active_pack"], stats["subscription"]
            if pack:
                kind, payment_status = "pack", pack["payment_status"]
            elif sub:
                kind, payment_status = "subscription", sub["payment_status"]
            else:
                kind, payment_status = "none", None
            upcoming = db.get_client_upcoming_bookings(client_id)
            return {
                "client_id": client_id,
                "name": c["name"],
                "remaining_sessions": pack["remaining_sessions"] if pack else 0,
                "package_kind": kind,
                "payment_status": payment_status,
                "can_book": bool(pack) or bool(sub),
                "upcoming_booking_slot_ids": set(upcoming["slot_id"].tolist()),
                "upcoming_count": len(upcoming),
            }
    return None


def is_client_booked_in_slot(slot_id: int, client_id: int) -> bool:
    """Guards the public self-booking flow against an accidental double
    booking (e.g. a double click) by checking the existing roster first."""
    bookings = db.get_slot_bookings(slot_id)
    if bookings.empty:
        return False
    client_bookings = bookings[bookings["booking_type"] == "client"]
    return client_id in client_bookings["client_id"].values


def find_client_booking_id(slot_id: int, client_id: int) -> int | None:
    """The booking row id for this client in this slot, if any — lets the
    public view offer a self-service Cancel action that reuses the same
    remove_slot_booking() the admin roster already uses (correctly restores
    a pack deduction), without exposing anyone else's booking id."""
    bookings = db.get_slot_bookings(slot_id)
    if bookings.empty:
        return None
    match = bookings[(bookings["booking_type"] == "client") &
                     (bookings["client_id"] == client_id)]
    return int(match.iloc[0]["id"]) if not match.empty else None


def book_client_to_slot(slot_id: int, client_id: int,
                        session_date: date | None = None) -> dict:
    """Assign a registered active client to a slot: deducts a session from
    their pack via the normal log_session() pipeline (FIFO deduction,
    overdraft handling, trainer/revenue-split attribution all reused as-is),
    then records the roster entry and bumps the slot's attendee count."""
    slot = db.fetch_schedule_slot(slot_id)
    if slot is None:
        raise ValidationError("Schedule slot not found.")
    if slot["current_attendees_count"] >= slot["max_capacity"]:
        raise ValidationError(
            "This slot is full / המקום מלא — increase capacity or choose "
            "another slot.")
    if is_client_booked_in_slot(slot_id, client_id):
        raise ValidationError(
            "This client is already booked into this slot / "
            "המתאמן/ת כבר רשומ/ה לאימון זה.")
    when = session_date or date.fromisoformat(slot["date"])
    result = log_session(
        client_id, when, notes=f"Schedule slot #{slot_id}",
        deduct=True, session_type=slot["session_type"],
        trainer=slot["coach_name"])
    db.add_schedule_booking(slot_id, client_id, None, "client",
                            session_id=result["session_id"])
    db.increment_slot_attendees(slot_id)
    return {"booking_type": "client", "session_result": result}


def book_dropin_to_slot(slot_id: int, dropin_name: str, price: float,
                        pay_method: str, session_date: date | None = None,
                        record_income: bool = True) -> dict:
    """Log a standalone drop-in (מזדמן) participant: charges a one-off
    price with no package involved. The income is recorded immediately and
    split by the payout engine's normal Frontal rule (70/10/20) using the
    slot's own coach — via a manual payout adjustment, since there's no
    package_id/session for compute_monthly_payouts to trace the trainer
    through automatically."""
    slot = db.fetch_schedule_slot(slot_id)
    if slot is None:
        raise ValidationError("Schedule slot not found.")
    if slot["current_attendees_count"] >= slot["max_capacity"]:
        raise ValidationError(
            "This slot is full / המקום מלא — increase capacity or choose "
            "another slot.")
    dropin_name = require_name(dropin_name)
    price = require_positive(price, "Drop-in price")
    when = session_date or date.fromisoformat(slot["date"])

    transaction_id = None
    if record_income:
        transaction_id = db.add_transaction(
            "Income", price, config.CATEGORY_FRONTAL, pay_method, when,
            None, notes=f"Drop-in: {dropin_name} — slot #{slot_id} "
                        f"({config.SESSION_TYPES.get(slot['session_type'], '')})")
        coach = slot["coach_name"]
        other = "Yuval" if coach == "Erez" else "Erez"
        erez_amt = (price * config.FRONTAL_CONDUCTING_PCT if coach == "Erez"
                   else price * config.FRONTAL_PARTNER_PCT)
        yuval_amt = (price * config.FRONTAL_CONDUCTING_PCT if coach == "Yuval"
                    else price * config.FRONTAL_PARTNER_PCT)
        savings_amt = price * config.SAVINGS_PCT
        add_payout_adjustment(
            when.strftime("%Y-%m"), erez_amt, yuval_amt, savings_amt,
            reason=f"Drop-in: {dropin_name} (slot #{slot_id}, "
                   f"{config.TRAINERS[coach]} conducting)",
            transaction_id=transaction_id)

    booking_id = db.add_schedule_booking(
        slot_id, None, dropin_name, "dropin", transaction_id=transaction_id)
    db.increment_slot_attendees(slot_id)
    return {"booking_type": "dropin", "booking_id": booking_id,
            "transaction_id": transaction_id}


def remove_slot_booking(booking_id: int) -> dict:
    """Remove someone from a slot's roster. If it was a balance-deduction
    booking, the linked session is deleted too (delete_session() correctly
    restores the pack) — reusing that tested logic rather than duplicating
    it. A drop-in's income transaction and payout adjustment are left
    intact (financial records are never silently reversed as a side
    effect); reverse those deliberately in Finance if truly needed."""
    booking = db.fetch_schedule_booking(booking_id)
    if booking is None:
        raise ValidationError("Booking not found.")
    session_id = booking["session_id"]
    # Delete the booking (which references the session) BEFORE the session
    # itself, or the foreign key constraint blocks the session delete.
    db.delete_schedule_booking(booking_id)
    session_restored = delete_session(session_id) if session_id else None
    db.decrement_slot_attendees(booking["slot_id"])
    return {"removed": True, "was_dropin": booking["booking_type"] == "dropin",
            "session_restored": session_restored}


def get_slot_bookings(slot_id: int) -> pd.DataFrame:
    return db.get_slot_bookings(slot_id)


# ---------------------------------------------------------------------------
# Dashboard quick-edit: diff + validate + persist
# ---------------------------------------------------------------------------
def _norm_num(v, as_int: bool = False):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    return int(v) if as_int else float(v)


def _norm_date(v):
    if v is None or pd.isna(v):
        return None
    if isinstance(v, str):
        return v[:10]
    return pd.Timestamp(v).date().isoformat()


def _norm_text(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).strip()
    return s or None


def apply_package_edits(orig_df: pd.DataFrame,
                        edited_df: pd.DataFrame) -> tuple[int, list]:
    """Diff the edited packages table against the original and persist valid
    changes. Returns (n_changed, errors)."""
    n, errors = 0, []
    orig = {int(r["id"]): r for _, r in orig_df.iterrows()}
    for _, row in edited_df.iterrows():
        pid = int(row["id"])
        o = orig[pid]
        new = (_norm_num(row["total_sessions"], True),
               _norm_num(row["remaining_sessions"], True),
               _norm_num(row["monthly_rate"]),
               _norm_date(row["start_date"]),
               _norm_num(row["price"]),
               _norm_num(row["amount_paid"]),
               _norm_text(row["payment_status"]))
        old = (_norm_num(o["total_sessions"], True),
               _norm_num(o["remaining_sessions"], True),
               _norm_num(o["monthly_rate"]),
               _norm_date(o["start_date"]),
               _norm_num(o["price"]),
               _norm_num(o["amount_paid"]),
               _norm_text(o["payment_status"]))
        if new == old:
            continue
        total, rem, rate, sd, price, paid, status = new
        who = f"{o['client_name']} — {o['package_type']}"
        if (total is None) != (rem is None):
            errors.append(f"{who}: total and remaining sessions must both be "
                          "set, or both left empty.")
            continue
        if total is not None and rem is not None and rem > total:
            errors.append(f"{who}: remaining ({rem}) cannot exceed "
                          f"total ({total}).")
            continue
        if sd is None:
            errors.append(f"{who}: start date is required.")
            continue
        if status not in config.PAYMENT_STATUSES:
            errors.append(f"{who}: payment status must be one of "
                          f"{', '.join(config.PAYMENT_STATUSES)}.")
            continue
        if price is not None and paid is not None and paid > price:
            errors.append(f"{who}: amount paid ({paid:.0f}) cannot exceed "
                          f"price ({price:.0f}).")
            continue
        db.update_package_fields(pid, total, rem, rate, sd, price=price,
                                 amount_paid=paid, payment_status=status)
        n += 1
    return n, errors


def apply_transaction_edits(orig_df: pd.DataFrame,
                            edited_df: pd.DataFrame) -> tuple[int, int, int, list]:
    """Diff the edited transactions table: apply updates, deletions and
    insertions. Returns (n_updated, n_deleted, n_added, errors)."""
    n_upd = n_del = n_add = 0
    errors = []
    orig = {int(r["id"]): r for _, r in orig_df.iterrows()}
    edited_ids = {int(i) for i in edited_df["id"].dropna()}

    for tx_id in set(orig) - edited_ids:          # removed rows
        db.delete_transaction(tx_id)
        n_del += 1

    for _, row in edited_df.iterrows():
        tx_type = _norm_text(row["type"])
        amount = _norm_num(row["amount"])
        category = _norm_text(row["category"])
        tx_date = _norm_date(row["date"])
        pay = _norm_text(row["payment_method"])
        notes = _norm_text(row["notes"]) or ""
        reference = _norm_text(row.get("reference_number"))

        if pd.isna(row["id"]):                    # newly added row
            if tx_type not in ("Income", "Expense") or not amount \
                    or amount <= 0 or not category:
                errors.append("New row skipped: type, positive amount and "
                              "category are required.")
                continue
            db.add_transaction(tx_type, amount, category, pay or "אחר",
                               date.fromisoformat(
                                   tx_date or date.today().isoformat()),
                               None, notes, reference)
            n_add += 1
            continue

        o = orig[int(row["id"])]
        old = (_norm_text(o["type"]), _norm_num(o["amount"]),
               _norm_text(o["category"]), _norm_text(o["payment_method"]),
               _norm_date(o["date"]), _norm_text(o["notes"]) or "",
               _norm_text(o.get("reference_number")))
        new = (tx_type, amount, category, pay, tx_date, notes, reference)
        if new == old:
            continue
        if tx_type not in ("Income", "Expense"):
            errors.append(f"Transaction #{int(row['id'])}: type must be "
                          "Income or Expense.")
            continue
        if not amount or amount <= 0:
            errors.append(f"Transaction #{int(row['id'])}: amount must be "
                          "greater than 0.")
            continue
        if not tx_date:
            errors.append(f"Transaction #{int(row['id'])}: date is required.")
            continue
        db.update_transaction_fields(int(row["id"]), tx_type, amount,
                                     category or "אחר", pay, tx_date, notes,
                                     reference)
        n_upd += 1
    return n_upd, n_del, n_add, errors


# ---------------------------------------------------------------------------
# Backup & export
# ---------------------------------------------------------------------------
def backup_db_bytes() -> bytes:
    """Consistent snapshot of the whole database via SQLite's backup API
    (safe even while the app is writing)."""
    src = db.get_connection()
    fd, tmp_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        dest = sqlite3.connect(tmp_path)
        with dest:
            src.backup(dest)
        dest.close()
        with open(tmp_path, "rb") as f:
            return f.read()
    finally:
        src.close()
        os.unlink(tmp_path)


def export_excel_bytes() -> bytes:
    """All core tables as one Excel workbook (Clients / Packages /
    Sessions / Finance), ready for download."""
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        db.get_clients().to_excel(writer, sheet_name="Clients", index=False)
        db.get_packages().to_excel(writer, sheet_name="Packages", index=False)
        db.get_sessions(limit=1_000_000).to_excel(
            writer, sheet_name="Sessions History", index=False)
        db.get_transactions().to_excel(writer, sheet_name="Finance",
                                       index=False)
        db.get_debts().to_excel(writer, sheet_name="Open Debts", index=False)
    return buf.getvalue()


def table_counts() -> dict:
    return db.table_counts()


RESET_CONFIRMATION_PHRASE = "DELETE ALL DATA"


def reset_database(confirmation_text: str) -> dict:
    """Wipe every client/package/session/transaction — irreversible.
    Requires the caller to pass back RESET_CONFIRMATION_PHRASE exactly, so a
    stray call (missing UI checkbox, a bug in a future caller) can never
    trigger a wipe by accident."""
    if (confirmation_text or "").strip() != RESET_CONFIRMATION_PHRASE:
        raise ValidationError(
            f"Type '{RESET_CONFIRMATION_PHRASE}' exactly to confirm — "
            "nothing was deleted.")
    return db.reset_all_data()
