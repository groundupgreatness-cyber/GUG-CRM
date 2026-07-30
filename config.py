"""
GUG CRM — Business configuration & pricing model.

All prices are in ILS. Edit this file to change pricing without touching
the database or UI code.
"""

# ---------------------------------------------------------------------------
# Service types
# ---------------------------------------------------------------------------
SERVICE_FRONTAL = "Frontal Training"
SERVICE_ONLINE = "Online Coaching"

SERVICE_TYPES = [SERVICE_FRONTAL, SERVICE_ONLINE]

# ---------------------------------------------------------------------------
# Frontal Training packages (one-time purchase, session based)
# key -> (label, total_sessions, price_ils)
# ---------------------------------------------------------------------------
FRONTAL_PACKAGES = {
    "single": {"label": "Single Session", "sessions": 1, "price": 180},
    "pass_5": {"label": "5-Session Pass", "sessions": 5, "price": 750},
    "pass_10": {"label": "10-Session Pass", "sessions": 10, "price": 1350},
    "mobility_75": {"label": "Mobility/Outdoor Single (75 ILS)",
                    "sessions": 1, "price": 75},
    "mobility_80": {"label": "Mobility/Outdoor Single (80 ILS)",
                    "sessions": 1, "price": 80},
}

# ---------------------------------------------------------------------------
# Session types for logged sessions: canonical key -> bilingual display label
# ---------------------------------------------------------------------------
SESSION_TYPES = {
    "Strength": "אימון כוח (Strength)",
    "Mobility": "מוביליטי (Mobility)",
    "Athletics": "אתלטיקה (Athletics)",
}

# ---------------------------------------------------------------------------
# Monthly memberships (frontal): monthly rate + a fixed number of included
# sessions that are deducted as they are logged. `quota` maps session types
# to their monthly allowance (used for the per-type breakdown display).
# ---------------------------------------------------------------------------
MONTHLY_MEMBERSHIPS = {
    "strength_mobility_1500": {
        "label": "Monthly: 8 Strength + 4 Mobility/Outdoor (1500 ILS)",
        "sessions": 12,
        "monthly_rate": 1500,
        "quota": {"Strength": 8, "Mobility": 4},
    },
    "strength_mobility_1350": {
        "label": "Monthly: 8 Strength + 4 Mobility/Outdoor (1350 ILS)",
        "sessions": 12,
        "monthly_rate": 1350,
        "quota": {"Strength": 8, "Mobility": 4},
    },
}

# ---------------------------------------------------------------------------
# Online Coaching subscriptions (monthly rate, minimum 3-month commitment)
# ---------------------------------------------------------------------------
ONLINE_MIN_COMMITMENT_MONTHS = 3

ONLINE_SUBSCRIPTIONS = {
    "full_online": {"label": "Full Online", "monthly_rate": 600},
    "hybrid": {"label": "Hybrid (Online + Frontal)", "monthly_rate": 1300},
}

# ---------------------------------------------------------------------------
# Finance defaults
# ---------------------------------------------------------------------------
# Standardized financial categories (Hebrew). Historical rows with older
# category names remain valid — edit tables always offer the union of these
# lists and any values already present in the data.
INCOME_CATEGORIES = [
    "אימונים פרונטליים",
    "ליווי אונליין",
    "מבדקי ביצועים",
    "פרויקטים/סדנאות",
    "אחר",
]

# Categories used automatically when packages/debts generate income
CATEGORY_FRONTAL = "אימונים פרונטליים"
CATEGORY_ONLINE = "ליווי אונליין"

# Pre-standardization category labels used by earlier versions of this app.
# The payout engine treats these as aliases so old income still allocates
# correctly instead of being silently skipped.
LEGACY_FRONTAL_CATEGORIES = {"Frontal Training"}
LEGACY_ONLINE_CATEGORIES = {"Online Coaching"}

# ---------------------------------------------------------------------------
# Payment statuses for packages/subscriptions: canonical key -> display label
# ---------------------------------------------------------------------------
PAYMENT_STATUSES = {
    "Paid": "🟢 שולם (Paid)",
    "Partial": "🟡 שולם חלקית (Partial)",
    "Pending": "🔴 חוב פתוח (Pending)",
}

# Display-only badge labels for client status (the stored value stays plain
# 'Active'/'Inactive' — comparisons and filters use the unprefixed key).
CLIENT_STATUS_BADGES = {
    "Active": "🟢 Active",
    "Inactive": "🟡 Inactive",
}

EXPENSE_CATEGORIES = [
    "שכירות מתחם/ציוד",
    "שיווק ופרסום",
    "תוכנות וטכנולוגיה",
    "עמלות סליקה/בנק",
    "נסיעות/רכב",
    "שכר/ספקים",
    "אחר",
]

PAYMENT_METHODS = [
    "העברה בנקאית",
    "מזומן",
    "אשראי",
    "ביט / פייבוקס",
]

# ---------------------------------------------------------------------------
# Revenue Split & Partner Payout Engine
# ---------------------------------------------------------------------------
# The two training partners. Canonical keys ('Erez'/'Yuval') are stored in
# the database; the values are the bilingual labels shown in the UI.
TRAINERS = {
    "Erez": "מאמן ארז (Coach Erez)",
    "Yuval": "מאמן יובל (Coach Yuval)",
}

# Frontal Training (packs & the 8-strength+4-mobility memberships): the
# trainer who conducted the session takes the "conducting" share, the other
# partner takes the "partner" share, and the rest goes to savings. The three
# must sum to 1.0.
FRONTAL_CONDUCTING_PCT = 0.70
FRONTAL_PARTNER_PCT = 0.10

# Full Online Coaching (and the online portion of Hybrid): split evenly
# between the two partners, with the remainder to savings. Must sum to 1.0
# together with SAVINGS_PCT.
ONLINE_EREZ_PCT = 0.40
ONLINE_YUVAL_PCT = 0.40

# Business Savings Fund (חיסכון העסק) — the same 20% applies under both the
# Frontal and Online rules, so a single shared constant keeps them in sync.
SAVINGS_PCT = 0.20

# Hybrid Online + Studio bundles studio sessions and online coaching into one
# flat monthly fee with no separate line-item price for the studio portion.
# This fixed share of the fee is treated as "frontal" revenue (split via the
# Frontal rule, attributed by trainer) and the remainder as "online" revenue
# (split via the Online rule). Tune to match real studio usage patterns.
HYBRID_FRONTAL_SHARE = 0.5

# Substring used to detect a Hybrid package/subscription by its stored
# package_type label (e.g. "Hybrid (Online + Frontal)").
HYBRID_LABEL_MARKER = "Hybrid"

# ---------------------------------------------------------------------------
# WhatsApp Quick-Action Messaging
# ---------------------------------------------------------------------------
# Every wa.me link built by logic.whatsapp_link() targets ONLY the recipient
# client's own phone number. There is no "sender"/"from" number anywhere in
# this config, deliberately — wa.me links have no such parameter (the
# message is sent from whichever WhatsApp account is logged into the device
# that opens the link), so encoding a coach's number here would be both
# meaningless and a standing invitation to leak it into a URL by mistake.

# {placeholders} are filled in by logic.whatsapp_message(). Keep the exact
# wording here — it's the message the client actually receives.
WHATSAPP_TEMPLATES = {
    "payment_reminder":
        "היי {name}, תזכורת קטנה מ-GUG לגבי יתרת תשלום פתוחה. "
        "נשמח להסדיר זאת בהזדמנות...",
    "low_sessions":
        "היי {name}, שים לב שנשאר לך עוד אימון אחד בלבד בכרטיסייה! "
        "לחדש לך אותה?",
    "session_confirmation":
        "היי {name}, אימון {session_type} נרשם בהצלחה. "
        "יתרה מעודכנת: {remaining} אימונים.",
    "inactivity":
        "היי {name}, ראיתי שלא התאמנו כבר שבועיים! "
        "מתי קובעים את האימון הבא?",
}

# How many days of no logged session before an active client is flagged as
# inactive (Dashboard → 🔔 Requires Attention → Inactivity).
INACTIVITY_THRESHOLD_DAYS = 14

# ---------------------------------------------------------------------------
# Weekly Schedule Management
# ---------------------------------------------------------------------------
# Hebrew day names, keyed by Python's date.weekday() (Monday=0 ... Sunday=6).
# Derived automatically from a slot's date so the two can never disagree.
HEBREW_DAY_NAMES = {
    6: "ראשון",   # Sunday
    0: "שני",     # Monday
    1: "שלישי",   # Tuesday
    2: "רביעי",   # Wednesday
    3: "חמישי",   # Thursday
    4: "שישי",    # Friday
    5: "שבת",     # Saturday
}

DEFAULT_SLOT_CAPACITY = 6

# How many days ahead the public client view and the default admin list show.
SCHEDULE_LOOKAHEAD_DAYS = 14

# The one legitimate case where Coach Erez's own number is the wa.me
# RECIPIENT: a client messaging him to request a slot. This is the opposite
# direction from every other WHATSAPP_TEMPLATES entry (which always target
# the client) — see logic.booking_whatsapp_link(), which is a separate,
# dedicated function from logic.whatsapp_link() specifically so a client's
# phone can never accidentally be swapped out for this one on a future edit.
COACH_BOOKING_PHONE_INTL = "972584584404"

WHATSAPP_TEMPLATES["booking_request"] = (
    "היי ארז, אשמח להירשם לאימון {session_type} ביום {day_of_week} "
    "({date}) בשעה {start_time}!"
)

# Static fallback shown on the public schedule when a visitor isn't a
# recognized client with remaining sessions (Step 1 self-identification).
# No placeholders — deliberately generic since we don't know who's asking.
WHATSAPP_TEMPLATES["renew_or_register"] = (
    "היי ארז, אשמח לחדש כרטיסייה / להירשם לאימון!"
)

# ---------------------------------------------------------------------------
# Admin authentication
# ---------------------------------------------------------------------------
# The real password lives in .streamlit/secrets.toml (admin_password = "..."),
# which is where Streamlit apps are meant to keep secrets — never commit
# actual credentials into a source file like this one. This constant is only
# a fallback so the app still runs (with a clear, changeable default) if
# that file is ever missing, rather than crashing on first launch.
DEFAULT_ADMIN_PASSWORD = "GUG-Coach-2026"
