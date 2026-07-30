# GUG CRM — Ground Up Greatness

A lightweight business management system built with **Streamlit** and **SQLite**.

## Access control

Unauthenticated visitors — anyone who opens the app without logging in —
see **only** the public schedule (open slots + WhatsApp booking button). No
sidebar page list, no client data, no financials, nothing else is reachable:
the router itself only ever calls `render_public_schedule_view()` for that
session (`app.py`, bottom) — hiding the sidebar nav is a UX nicety, not the
actual boundary.

Coach Erez and Coach Yuval share one admin password (there are no separate
accounts) to unlock the full CRM — sidebar → **🔐 Coach Login**. The real
password lives in `.streamlit/secrets.toml` (`admin_password = "..."`,
**not** committed anywhere, edit it directly to change it); if that file is
ever missing, `config.DEFAULT_ADMIN_PASSWORD` is used as a fallback so the
app doesn't crash on a fresh checkout. Log out anytime via the sidebar's
**🔓 Logout** button, which immediately re-locks the session.

**Default password:** `GUG-Coach-2026` — change it in `.streamlit/secrets.toml`
before sharing this app's URL with anyone.

## Features

- **Clients & Packages** — register clients, assign session packs or online
  subscriptions, and see remaining-session balances at a glance.
- **Log & Calendar** — pick an active client, log a completed session with a
  session type and trainer, and 1 session is automatically deducted from
  their pack (FIFO across packs); view everything on an interactive calendar.
- **Schedule** — weekly slot management for both coaches, plus a public
  client-facing schedule view with 1-click WhatsApp booking (see below).
- **Finance** — track income and expenses with categories, payment methods,
  reference numbers, notes, monthly/type/category filtering, a Debts tab,
  and a Partner Payouts tab (see below).
- **Overview Dashboard** — active clients, sessions this month, P&L, and a
  **🔔 Requires Attention / דורש טיפול** section (see below) with one-click
  WhatsApp buttons for pending payments, low session balances, and inactive
  clients.
- **Settings** — one-click database backup, a consolidated Excel export, and
  a guarded **Reset Database** danger zone (checkbox + typed confirmation
  phrase) for wiping all data and starting fresh while keeping the schema.

### Weekly Schedule Management & Public Booking

The public, unauthenticated landing page IS the read-only schedule view.
Once logged in, the **Schedule** page additionally offers an internal
toggle between two views of the same data (mainly so an admin can preview
exactly what a client sees, without logging out). Both views render as a
**visual weekly timetable grid** — day columns (ראשון–שבת) built with real
`st.columns(7)` containers (not raw HTML, so buttons/popovers nest inside
correctly), forced to scroll horizontally instead of stacking on narrow
screens via a CSS rule scoped to `st-key-week_grid_public`/`_admin`
(`div[class*="st-key-week_grid_..."] [data-testid="stHorizontalBlock"] {
flex-wrap: nowrap; overflow-x: auto; }`). A shared `◀ / ▶` week-navigator
(`app._week_nav`, offset held in `st.session_state`) pages through the
public view's `SCHEDULE_LOOKAHEAD_DAYS` window and — for admins with
"Include past slots" checked — arbitrarily far back/forward, bounded by
the actual min/max slot dates in the data. Slot cards are color-coded by
session type via the existing `SESSION_TYPE_COLORS` palette (same hues as
the charts/calendar) as a left-border accent, built once by
`app._slot_card_html()` and shared by both grids so they can never
visually disagree. `logic.slots_by_date()` groups a date range's slots
into the fixed 7 calendar days (some possibly empty, rendered as a muted
"אין אימונים" placeholder) so the two views can't disagree on ordering
either.

- **Admin grid** — each card shows the roster (client/drop-in names)
  directly inline, plus four quick-action controls: **✏️** and **➕**/**💵**
  open a small `st.popover` with the edit-slot form / add-active-client
  picker / add-drop-in form respectively (reusing the exact same
  `create_schedule_slot`/`book_client_to_slot`/`book_dropin_to_slot` logic
  as before — deducts a session from the pack via the normal FIFO logic,
  fully integrated with trainer/revenue-split attribution, or logs a
  standalone **Drop-in / מזדמן** straight to `financial_transactions` +
  a manual payout adjustment using the slot's own coach); **🗑️** is a
  two-step confirm (not a bare click) since deleting a slot does **not**
  reverse attendees' pack deductions — a real risk now that the action is
  one click away on a much more visible card instead of buried in a nested
  expander. A "👥 נרשמים (N)" expander below the quick actions still holds
  per-attendee removal (reverses a client's pack deduction; a drop-in's
  income record is left intact, financial records are never auto-reversed).
- **Public grid** — each open slot's card shows an optional
  **phone-number lookup** (`logic.find_client_by_phone`) above the grid —
  deliberately a phone box, not a name dropdown, since a dropdown would
  have to list every client publicly. A recognized client with sessions
  remaining sees a green **"✅ אני רוצה להירשם"** button directly on the
  card that books them on the spot (same `book_client_to_slot` pipeline
  the admin view uses, guarded against double-booking the same slot); an
  unrecognized or zero-balance visitor instead sees a friendly nudge with
  a WhatsApp button to renew, and the slot's card falls back to the
  original **"בקשת הרשמה"** WhatsApp request button. The lookup only ever
  surfaces that one client's own name/balance — never anything about
  anyone else.

Two tables back this: `weekly_schedule` (the slots) and `schedule_bookings`
(who's in each slot — needed because "assign a client or log a drop-in"
requires tracking individuals, not just a headcount). `current_attendees_count`
is a cached counter kept in sync by the booking/removal functions, the same
pattern `packages.remaining_sessions` already uses.

### WhatsApp Quick-Action Messaging & Inactivity Alerts

Every client-facing list in the app (Client List's Quick Contact panel,
Dashboard alert tabs, the Log & Calendar session confirmation, and the
Recent Sessions history) can offer a green **💬 WhatsApp** button that opens
`wa.me/<phone>?text=<message>` in a new tab, pre-filled with one of four
Hebrew templates (`config.WHATSAPP_TEMPLATES`): Payment Reminder, Low
Sessions Alert, Session Confirmation, and Inactivity/Re-engagement.

- **Phone formatting** (`logic.format_phone_intl`) accepts any common
  Israeli format (leading 0, dashes, spaces, `+972`, already-international)
  and normalizes it to WhatsApp's expected digits-only international form.
  An unrecognizable or missing number gracefully hides the button ("📵 No
  phone") instead of linking to a broken chat.
- **Inactivity detection** (`logic.detect_inactive_clients`, default
  `config.INACTIVITY_THRESHOLD_DAYS = 14`) flags active clients whose most
  recent logged session is 14+ days old. Clients with *no* session history
  at all are excluded on purpose — they haven't gone quiet, they just
  haven't started.
- **Recipient-only links, guaranteed** — every generated URL is exactly
  `https://wa.me/<recipient-intl-digits>?text=<encoded-message>`: one path
  segment, one query parameter, always the *client's* number. WhatsApp
  click-to-chat links have no "from"/"sender" parameter at all (the message
  opens from whichever WhatsApp account is logged into the device that
  clicks the button), so there's never a coach phone number anywhere in the
  config or the URL — nothing to encode, nothing to leak.

### Revenue Split & Partner Payout Engine

Every logged session records who conducted it (Coach Erez / Coach Yuval).
The **Partner Payouts** tab (Finance → 💼) computes, for any month, how much
of that month's income belongs to each partner and to the Business Savings
Fund (חיסכון העסק):

| Revenue type | Split |
|---|---|
| Frontal Training (packs & the 8-strength+4-mobility memberships) | 70% conducting trainer / 10% partner / 20% savings |
| Full Online Coaching | 40% Erez / 40% Yuval / 20% savings |
| Hybrid Online + Studio | `HYBRID_FRONTAL_SHARE` (config.py, default 50%) of the fee uses the Frontal rule (trainer-attributed via sessions logged that month); the rest uses the Online rule |

If a Frontal package's income has no trainer-tagged sessions yet, its 80%
Erez/Yuval portion is held as **pending** (savings is still allocated
immediately) rather than guessed — it resolves itself once a session is
logged, or can be settled with a **Manual Payout Adjustment** (also in the
Partner Payouts tab), which requires a reason for the audit trail.

## Visual design system

The app follows a colorblind-validated categorical palette (see the
project's `dataviz` skill), applied consistently everywhere a 2–3-way split
appears, defined once in `app.py`:

- **Charts** — three Plotly visuals on the Overview Dashboard: a Session
  Types donut (Strength/Mobility/Athletics), a Revenue Split donut (Erez /
  Yuval / Savings, reusing the payout engine), and an Income vs Expenses
  grouped bar chart (last 6 months). Same hues as the calendar, so "blue"
  always means the same session type everywhere.
- **KPI cards** — custom rounded HTML/CSS cards with an accent color, icon,
  and hover lift, used for the dashboard's headline metrics; native
  `st.metric` elsewhere gets the same card treatment via CSS.
- **Status badges** — 🟢 green (Active client / Paid), 🟡 yellow (Inactive /
  Partial payment), 🔴 red (Pending debt / Overdraft session), shown as
  colored-dot prefixes inside `st.dataframe` tables so search/sort keep
  working (Streamlit's data grid doesn't render HTML per-cell).
- **Bilingual-safe styling** — labels are "English / עברית" throughout, so
  text direction uses `unicode-bidi: plaintext` (auto-detects each block's
  direction from its own content) rather than a forced `direction: rtl`,
  which would reorder the English/Hebrew halves of every label.
- **Theme-aware** — both light and dark mode (detected via
  `st.context.theme.type`) get their own validated color steps; chart
  backgrounds are transparent so they blend into whichever theme is active.

## Pricing model (config.py)

| Service | Option | Price |
|---|---|---|
| Frontal Training | Single Session | 180 ILS |
| Frontal Training | 5-Session Pass | 750 ILS |
| Frontal Training | 10-Session Pass | 1,350 ILS |
| Frontal Training | Mobility/Outdoor Single | 75 ILS |
| Frontal Training | Mobility/Outdoor Single | 80 ILS |
| Monthly Membership | 8 Strength + 4 Mobility/Outdoor (12 sessions) | 1,500 ILS/month |
| Monthly Membership | 8 Strength + 4 Mobility/Outdoor (12 sessions) | 1,350 ILS/month |
| Online Coaching (min. 3 months) | Full Online | 600 ILS/month |
| Online Coaching (min. 3 months) | Hybrid (Online + Frontal) | 1,300 ILS/month |

Edit `config.py` to change prices, categories, or payment methods — no other
code changes needed.

## Project structure

```
gug crm/
├── app.py              # Streamlit presentation layer (entry point)
├── logic.py            # Business logic: balances, payments, validation,
│                       #   overdraft, edit-diffs, backup/export
├── database.py         # SQLite data layer: connection, schema, migrations,
│                       #   parameterized queries, indexes
├── config.py           # Pricing model & business constants
├── import_gug_data.py  # One-time historical import from the business sheet CSV
├── reset_db.py         # Wipe all data (schema kept); requires --yes
├── requirements.txt    # Dependencies
└── gug_crm.db          # SQLite database (created automatically on first run)
```

Layering rule: `app.py → logic.py → database.py`. The UI holds no business
rules and writes no SQL; all user-input problems surface as friendly
messages via `logic.ValidationError`. Backups and Excel exports live in
the app's **Settings / גיבוי והגדרות** page.

## Historical data import

`import_gug_data.py` loads the Google-Sheets export
"דף עסק GUG - תמחורים_דו״ח הכנסות.csv" into the database: clients and
punch-card packages, online-coaching subscriptions and sales, monthly
income / rent (Jan–June 2026), and the daily training logs (Jan–July 2026)
as session records. It refuses to run if the database already contains
clients; `--force` wipes the data tables and re-imports from the CSV:

```bash
python import_gug_data.py          # first import
python import_gug_data.py --force  # wipe & re-import
```

## Setup & run

```bash
# 1. (Recommended) create a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Run the app
streamlit run app.py
```

The app opens in your browser at http://localhost:8501. The database file
`gug_crm.db` is created automatically the first time the app starts.
