"""
GUG CRM — Streamlit presentation layer ONLY.

Layering rule:  app.py ──► logic.py (business rules) ──► database.py (SQL).
This module renders widgets, routes user actions to logic.py, and shows
results; it holds no business rules and writes no SQL.

Run from this folder with:

    streamlit run app.py

Access control: unauthenticated visitors see ONLY the public schedule (no
sidebar navigation, no CRM data). Coach Erez/Yuval log in with a shared
password (see .streamlit/secrets.toml) to unlock the full sidebar:
    1. Clients & Packages   — register clients, assign packages, view balances
    2. Log & Calendar       — log sessions + interactive calendar, side by side
    3. Schedule             — slot management (admin) / public preview toggle
    4. Finance              — income, expenses & debt collection
    5. Overview Dashboard   — key numbers at a glance + quick-edit mode
    6. Settings             — database backup & Excel export
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from streamlit_calendar import calendar as st_calendar

import config
import database as db
import logic

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------
if "is_admin" not in st.session_state:
    st.session_state["is_admin"] = False

st.set_page_config(
    page_title="GUG CRM", page_icon=":material/fitness_center:", layout="wide",
    initial_sidebar_state="expanded" if st.session_state["is_admin"] else "collapsed",
)
db.init_db()


def _check_admin_password(entered: str) -> bool:
    """Compare against .streamlit/secrets.toml's admin_password, falling
    back to config.DEFAULT_ADMIN_PASSWORD only if that file/key is missing
    (e.g. a fresh checkout before secrets.toml has been created)."""
    try:
        real = st.secrets["admin_password"]
    except Exception:
        real = config.DEFAULT_ADMIN_PASSWORD
    return bool(entered) and entered == real

# ---------------------------------------------------------------------------
# Design system: palette, CSS injection, small components.
#
# Fixed high-tech dark performance identity (spellmanperformance.com-style) —
# a deliberate brand choice, not adaptive to a visitor's OS light/dark
# setting. .streamlit/config.toml pins base="dark" to match.
#
# Colors follow a colorblind-validated categorical palette, used consistently
# everywhere a 2-3-way split appears (session types, coach payouts, income vs
# expense) so the same hue always means "first/primary" across the whole app.
# Status colors (good/warning/critical) are reserved for state — client
# status, payment status, session overdraft — and never reused as a chart
# series color. See the dataviz skill for the source palette.
# ---------------------------------------------------------------------------
COLOR_BG = "#0D0E12"
COLOR_CARD_BG = "#1A1D24"
COLOR_CARD_BORDER = "#2A2E39"
COLOR_TEXT_PRIMARY = "#FFFFFF"
COLOR_TEXT_MUTED = "#9A9FAE"
COLOR_ACCENT = "#E0610E"
COLOR_ACCENT_HOVER = "#B34E0B"

# One consistent 3-color language reused by the calendar, the session-types
# donut, and (separately) the payout donut — never mixed on the same chart.
# Deliberately independent from COLOR_ACCENT (brand/UI-chrome color): these
# encode data categories, not brand identity, and COLOR_ORANGE already
# occupies this exact palette slot, so aliasing it to the (now orange)
# accent would collide the two categories into one indistinguishable hue.
COLOR_BLUE = "#0066FF"
COLOR_ORANGE = "#FF8A00"
COLOR_AQUA = "#14D8C4"

COLOR_GOOD = "#22C55E"
COLOR_WARNING = "#F5A623"
COLOR_CRITICAL = "#EF4444"

CHART_MUTED_INK = COLOR_TEXT_MUTED
CHART_GRIDLINE = COLOR_CARD_BORDER

SESSION_TYPE_COLORS = {
    "Strength": COLOR_BLUE, "Mobility": COLOR_AQUA, "Athletics": COLOR_ORANGE,
}
PAYOUT_COLORS = {"Erez": COLOR_BLUE, "Yuval": COLOR_ORANGE, "Savings": COLOR_AQUA}


def inject_custom_css():
    st.markdown(
        f"""
        <style>
        /* ---- Typography & spacing --------------------------------------- */
        html, body, [class*="css"] {{
            font-family: -apple-system, "Segoe UI", system-ui, sans-serif;
        }}
        h1, h2, h3 {{
            letter-spacing: 0.02em;
            text-transform: uppercase;
            font-weight: 800;
            color: {COLOR_TEXT_PRIMARY};
        }}
        [data-testid="stMainBlockContainer"] {{ padding-top: 2rem; }}

        /* ---- Auto-direction for mixed Hebrew/English text -----------------
           Nearly every label in this app is bilingual ("English / עברית"),
           so forcing direction:rtl reorders the two halves and breaks them
           (confirmed visually). unicode-bidi:plaintext instead auto-detects
           each block's base direction from its own first strong character —
           a pure-Hebrew note or name reads right-to-left; a bilingual
           "Label / תווית" stays left-to-right, exactly as authored. This is
           the CSS equivalent of the HTML dir="auto" attribute. */
        [data-testid="stMarkdownContainer"],
        [data-testid="stCaptionContainer"],
        [data-testid="stAlertContainer"],
        [data-testid="stWidgetLabel"] {{
            unicode-bidi: plaintext;
        }}
        [data-testid="stCaptionContainer"] {{ color: {COLOR_TEXT_MUTED} !important; }}

        /* ---- Modern card look for native metrics -------------------------- */
        [data-testid="stMetric"] {{
            background: {COLOR_CARD_BG};
            border: 1px solid {COLOR_CARD_BORDER};
            border-radius: 6px;
            padding: 14px 18px;
            transition: border-color .15s ease;
        }}
        [data-testid="stMetric"]:hover {{ border-color: {COLOR_ACCENT}; }}
        [data-testid="stMetricLabel"] {{
            text-transform: uppercase;
            letter-spacing: .04em;
            font-size: 12px !important;
            color: {COLOR_TEXT_MUTED} !important;
        }}
        [data-testid="stMetricValue"] {{ font-weight: 800 !important; }}

        /* ---- Sharp, breathable containers ---------------------------------- */
        [data-testid="stDataFrame"], [data-testid="stDataFrameResizable"] {{
            border-radius: 6px;
            overflow: hidden;
            border: 1px solid {COLOR_CARD_BORDER};
        }}
        [data-testid="stExpander"] {{
            border-radius: 6px !important;
            border: 1px solid {COLOR_CARD_BORDER} !important;
            background: {COLOR_CARD_BG} !important;
        }}
        [data-testid="stForm"] {{
            border-radius: 6px;
            border: 1px solid {COLOR_CARD_BORDER};
            background: {COLOR_CARD_BG};
            padding: 1.2rem 1.2rem 0.4rem;
        }}

        /* ---- Buttons: flat, sharp, bold, high-contrast hover --------------- */
        .stButton > button, .stFormSubmitButton > button, .stDownloadButton > button {{
            border-radius: 5px;
            font-weight: 700;
            letter-spacing: .02em;
            text-transform: uppercase;
            border: 1px solid {COLOR_CARD_BORDER};
            transition: background .12s ease, border-color .12s ease, color .12s ease;
        }}
        .stButton > button:hover, .stFormSubmitButton > button:hover,
        .stDownloadButton > button:hover {{
            border-color: {COLOR_ACCENT};
            color: {COLOR_ACCENT};
        }}
        .stButton > button[kind="primary"], .stFormSubmitButton > button[kind="primary"] {{
            background: {COLOR_ACCENT};
            border-color: {COLOR_ACCENT};
        }}
        .stButton > button[kind="primary"]:hover,
        .stFormSubmitButton > button[kind="primary"]:hover {{
            background: {COLOR_ACCENT_HOVER};
            border-color: {COLOR_ACCENT_HOVER};
            color: {COLOR_TEXT_PRIMARY};
        }}

        /* ---- Tabs: sharp underline style ------------------------------------ */
        .stTabs [data-baseweb="tab-list"] {{ gap: 4px; }}
        .stTabs [data-baseweb="tab"] {{
            border-radius: 4px 4px 0 0;
            padding: 8px 16px;
            text-transform: uppercase;
            font-weight: 700;
            letter-spacing: .02em;
            font-size: 13px;
        }}
        .stTabs [aria-selected="true"] {{ color: {COLOR_ACCENT} !important; }}

        /* ---- Sidebar navigation: comfortable click targets ----------------- */
        [data-testid="stSidebar"] {{
            background: {COLOR_BG};
            border-inline-end: 1px solid {COLOR_CARD_BORDER};
        }}
        [data-testid="stSidebar"] [data-testid="stRadio"] label {{
            border-radius: 5px;
            padding: 6px 8px;
            transition: background .12s ease;
        }}
        [data-testid="stSidebar"] [data-testid="stRadio"] label:hover {{
            background: {COLOR_CARD_BG};
        }}
        [data-testid="stSidebar"] [data-testid="stRadio"] label[data-checked="true"] {{
            background: rgba(224, 97, 14, 0.14);
            border-inline-start: 3px solid {COLOR_ACCENT};
        }}

        /* ---- WhatsApp quick-action button ----------------------------------- */
        .gug-wa-btn {{
            display: inline-flex;
            align-items: center;
            gap: 6px;
            background: #25D366;
            color: white !important;
            padding: 6px 14px;
            border-radius: 5px;
            font-size: 12.5px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: .02em;
            text-decoration: none !important;
            white-space: nowrap;
            unicode-bidi: plaintext;
            transition: filter .12s ease;
        }}
        .gug-wa-btn:hover {{ filter: brightness(1.1); color: white !important; }}

        /* ---- Public schedule cards (mobile-friendly) ------------------------ */
        .gug-schedule-day {{
            font-size: 14px;
            font-weight: 800;
            text-transform: uppercase;
            letter-spacing: .03em;
            color: {COLOR_TEXT_MUTED};
            margin: 20px 0 8px;
            unicode-bidi: plaintext;
        }}
        .gug-schedule-card {{
            background: {COLOR_CARD_BG};
            border: 1px solid {COLOR_CARD_BORDER};
            border-radius: 6px;
            padding: 14px 16px;
            margin-bottom: 10px;
            unicode-bidi: plaintext;
        }}
        .gug-schedule-card-title {{
            font-size: 15px;
            font-weight: 700;
            margin-bottom: 4px;
            color: {COLOR_TEXT_PRIMARY};
        }}
        .gug-schedule-card-meta {{
            font-size: 13px;
            color: {COLOR_TEXT_MUTED};
            margin-bottom: 2px;
        }}
        .gug-schedule-seats {{
            display: inline-block;
            margin-top: 6px;
            font-size: 11.5px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: .02em;
            padding: 2px 10px;
            border-radius: 4px;
        }}
        .gug-schedule-seats-open {{
            background: rgba(34, 197, 94, 0.14);
            color: {COLOR_GOOD};
        }}
        .gug-schedule-seats-full {{
            background: rgba(239, 68, 68, 0.14);
            color: {COLOR_CRITICAL};
        }}

        /* ---- Weekly timetable grid (public + admin) ------------------------- */
        .gug-week-nav-range {{
            text-align: center;
            font-weight: 800;
            font-size: 13px;
            text-transform: uppercase;
            letter-spacing: .03em;
            padding-top: 6px;
            unicode-bidi: plaintext;
        }}
        .gug-week-day-header {{
            text-align: center;
            font-weight: 800;
            font-size: 12.5px;
            text-transform: uppercase;
            letter-spacing: .02em;
            padding: 6px 4px;
            border-radius: 5px;
            background: {COLOR_CARD_BG};
            border: 1px solid {COLOR_CARD_BORDER};
            margin-bottom: 8px;
            unicode-bidi: plaintext;
        }}
        .gug-week-day-date {{
            font-weight: 500;
            font-size: 11px;
            color: {COLOR_TEXT_MUTED};
            text-transform: none;
        }}
        .gug-week-empty {{
            text-align: center;
            font-size: 12px;
            color: {COLOR_TEXT_MUTED};
            padding: 14px 4px;
            unicode-bidi: plaintext;
        }}
        .gug-slot-block {{
            background: {COLOR_CARD_BG};
            border: 1px solid {COLOR_CARD_BORDER};
            border-inline-start: 3px solid {COLOR_TEXT_MUTED};
            border-radius: 5px;
            padding: 8px 10px;
            margin-bottom: 6px;
            unicode-bidi: plaintext;
        }}
        /* A slot the identified client is already registered for. */
        .gug-slot-block-registered {{
            border-color: {COLOR_GOOD};
            background: rgba(34, 197, 94, 0.08);
        }}
        .gug-slot-time {{ font-size: 12.5px; font-weight: 700; color: {COLOR_TEXT_PRIMARY}; }}
        .gug-slot-type {{
            font-size: 11.5px; font-weight: 700; margin-top: 1px;
            text-transform: uppercase; letter-spacing: .02em;
        }}
        .gug-slot-coach {{ font-size: 11.5px; color: {COLOR_TEXT_MUTED}; margin-top: 1px; }}
        .gug-slot-roster {{
            font-size: 11px;
            color: {COLOR_TEXT_MUTED};
            margin: -2px 0 6px 2px;
            unicode-bidi: plaintext;
        }}
        /* Force the grid's column row to scroll horizontally instead of
           stacking vertically on narrow screens — a "swipe the week" mobile
           timetable rather than one long stacked column. */
        div[class*="st-key-week_grid_"] [data-testid="stHorizontalBlock"] {{
            flex-wrap: nowrap !important;
            overflow-x: auto !important;
            padding-bottom: 8px;
        }}
        div[class*="st-key-week_grid_"] [data-testid="stColumn"],
        div[class*="st-key-week_grid_"] [data-testid="column"] {{
            min-width: 148px !important;
            flex: 0 0 148px !important;
        }}

        /* ---- Public self-booking button (Step 1 client identification) ----- */
        div[class*="st-key-selfbook_"] button {{
            background-color: {COLOR_ACCENT} !important;
            color: white !important;
            border: 1px solid {COLOR_ACCENT} !important;
            font-weight: 700 !important;
        }}
        div[class*="st-key-selfbook_"] button:hover {{
            background-color: {COLOR_ACCENT_HOVER} !important;
            border-color: {COLOR_ACCENT_HOVER} !important;
        }}

        /* ---- Custom KPI cards ---------------------------------------------- */
        .gug-kpi-row {{
            display: flex;
            gap: 12px;
            flex-wrap: wrap;
            margin: 4px 0 18px;
        }}
        .gug-kpi-card {{
            flex: 1 1 170px;
            min-width: 155px;
            background: {COLOR_CARD_BG};
            border: 1px solid {COLOR_CARD_BORDER};
            border-radius: 6px;
            padding: 16px 18px;
            transition: border-color .15s ease;
            unicode-bidi: plaintext;
        }}
        .gug-kpi-card:hover {{ border-color: var(--gug-kpi-accent, {COLOR_ACCENT}); }}
        .gug-kpi-label {{
            font-size: 11.5px; font-weight: 700; letter-spacing: .04em;
            text-transform: uppercase; color: {COLOR_TEXT_MUTED};
            margin-bottom: 6px; unicode-bidi: plaintext;
        }}
        .gug-kpi-value {{
            font-size: 26px; font-weight: 800; line-height: 1.15;
            color: {COLOR_TEXT_PRIMARY};
        }}
        .gug-kpi-sub {{
            font-size: 12px; color: {COLOR_TEXT_MUTED}; margin-top: 3px;
            unicode-bidi: plaintext;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


inject_custom_css()

ACCENT_CLASS = {"blue": COLOR_BLUE, "orange": COLOR_ORANGE, "aqua": COLOR_AQUA,
                "good": COLOR_GOOD, "warning": COLOR_WARNING,
                "critical": COLOR_CRITICAL}


def kpi_row(cards: list[dict]):
    """Render a responsive row of custom KPI cards. Each dict:
    {'label': str, 'value': str, 'sub': str (optional),
    'accent': one of ACCENT_CLASS}. No icons — the accent-colored left
    border and uppercase label carry the visual distinction instead."""
    html = ['<div class="gug-kpi-row">']
    for c in cards:
        accent = ACCENT_CLASS.get(c.get("accent"), COLOR_TEXT_MUTED)
        sub = (f'<div class="gug-kpi-sub">{c["sub"]}</div>'
               if c.get("sub") else "")
        html.append(
            f'<div class="gug-kpi-card" style="border-inline-start:3px solid {accent};'
            f'--gug-kpi-accent:{accent};">'
            f'<div class="gug-kpi-label">{c["label"]}</div>'
            f'<div class="gug-kpi-value">{c["value"]}</div>'
            f'{sub}</div>'
        )
    html.append("</div>")
    st.markdown("".join(html), unsafe_allow_html=True)


def whatsapp_button(label: str, url: str | None):
    """Render a green WhatsApp click-to-chat button. `url` must already be
    a complete logic.whatsapp_link() result (recipient-only, no 'from'
    parameter) — this function only wraps it in an anchor tag, it never
    edits or appends to the URL. Opens in a new tab
    (target="_blank" + rel="noopener noreferrer"). Shows a muted note
    instead of a dead link when the client has no valid phone number."""
    if not url:
        st.caption("No phone")
        return
    st.markdown(
        f'<a href="{url}" target="_blank" rel="noopener noreferrer" '
        f'class="gug-wa-btn">{label}</a>',
        unsafe_allow_html=True,
    )


def _chart_layout(fig: go.Figure, height: int = 320, title: str | None = None):
    """Shared clean layout: transparent surface (blends with the Streamlit
    page), hairline muted grid, system font, no toolbar chrome."""
    fig.update_layout(
        height=height,
        margin=dict(l=8, r=8, t=44 if title else 12, b=8),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="system-ui, -apple-system, 'Segoe UI', sans-serif",
                  color=CHART_MUTED_INK, size=13),
        title=(dict(text=title, font=dict(size=15), x=0.02, xanchor="left")
               if title else None),
        legend=dict(orientation="h", yanchor="bottom", y=-0.22, x=0.5,
                    xanchor="center", font=dict(size=12)),
        showlegend=True,
    )
    return fig


def donut_chart(labels: list[str], values: list[float], colors: list[str],
                title: str, unit: str = "₪", center_text: str | None = None):
    """Part-to-whole donut for ≤6 segments (session types, revenue split)."""
    hover = (f"%{{label}}: {unit}%{{value:,.0f}} (%{{percent}})<extra></extra>"
             if unit == "₪" else
             "%{label}: %{value:,.0f} " + unit + " (%{percent})<extra></extra>")
    fig = go.Figure(data=[go.Pie(
        labels=labels, values=values, hole=0.62,
        marker=dict(colors=colors, line=dict(color="rgba(0,0,0,0)", width=2)),
        textinfo="percent", textfont=dict(size=13, color="white"),
        hovertemplate=hover, sort=False,
    )])
    if center_text:
        fig.add_annotation(text=center_text, x=0.5, y=0.5, showarrow=False,
                           font=dict(size=15, color=CHART_MUTED_INK))
    return _chart_layout(fig, height=300, title=title)


def income_expense_bar_chart(df: pd.DataFrame) -> go.Figure:
    """Grouped bar, single ₪ axis — Income vs Expenses across months."""
    fig = go.Figure()
    fig.add_bar(x=df["month"], y=df["income"], name="Income / הכנסות",
               marker_color=COLOR_BLUE, marker_line_width=0,
               hovertemplate="%{x}<br>Income: ₪%{y:,.0f}<extra></extra>")
    fig.add_bar(x=df["month"], y=df["expense"], name="Expenses / הוצאות",
               marker_color=COLOR_ORANGE, marker_line_width=0,
               hovertemplate="%{x}<br>Expenses: ₪%{y:,.0f}<extra></extra>")
    fig.update_layout(barmode="group", bargap=0.35, bargroupgap=0.12)
    # Force categorical x-axis: with sparse month data (e.g. only 1-2 months
    # of history), Plotly's auto-detection otherwise infers a date axis and
    # renders an oversized "00:00:00 / Jul 1, 2026" tick instead of "2026-07".
    fig.update_xaxes(type="category", showgrid=False,
                     tickfont=dict(color=CHART_MUTED_INK))
    fig.update_yaxes(showgrid=True, gridcolor=CHART_GRIDLINE, gridwidth=1,
                     tickprefix="₪", tickformat=",.0f", zeroline=False)
    return _chart_layout(fig, height=340, title="Income vs Expenses — Last 6 Months")


st.sidebar.title("GUG CRM")

if st.session_state["is_admin"]:
    # --- Authenticated: full CRM navigation -------------------------------
    page = st.sidebar.radio(
        "Navigation",
        [
            "Clients & Packages",
            "Log & Calendar / תיעוד ויומן אימונים",
            "Schedule / מערכת שעות",
            "Finance (Income & Expenses)",
            "Overview Dashboard",
            "Settings / גיבוי והגדרות",
        ],
    )
    st.sidebar.divider()
    if st.sidebar.button("Logout / התנתקות"):
        st.session_state["is_admin"] = False
        st.rerun()
    st.sidebar.caption("Ground Up Greatness — business management")
else:
    # --- Not authenticated: no page list, no CRM data reachable at all —
    # the router below renders ONLY the public schedule for this branch,
    # and nothing here exposes a way to reach any other page. -------------
    page = None
    st.sidebar.caption("לוח אימונים ציבורי / Public schedule")
    with st.sidebar.expander("Coach Login / כניסת מאמנים"):
        with st.form("admin_login", clear_on_submit=True):
            entered_pw = st.text_input("Password / סיסמה", type="password")
            login_submitted = st.form_submit_button("Login")
        if login_submitted:
            if _check_admin_password(entered_pw):
                st.session_state["is_admin"] = True
                st.rerun()
            else:
                st.error("Incorrect password / סיסמה שגויה.")


# ---------------------------------------------------------------------------
# Cached reads — the cache key combines a per-session write counter with the
# database file's mtime, so caches refresh both after in-app writes AND when
# the database changes from outside this session. UI stays fast as history
# grows while always reflecting the latest data.
# ---------------------------------------------------------------------------
def _ver():
    try:
        mtime = os.path.getmtime(db.DB_PATH)
    except OSError:
        mtime = 0.0
    return (st.session_state.get("data_version", 0), mtime)


def bump_data() -> None:
    """Call after ANY database write so cached reads refresh."""
    st.session_state["data_version"] = (
        st.session_state.get("data_version", 0) + 1)


@st.cache_data(show_spinner=False)
def cached_clients(ver, status=None) -> pd.DataFrame:
    return db.get_clients(status)


@st.cache_data(show_spinner=False)
def cached_calendar_sessions(ver, client_id, session_type) -> pd.DataFrame:
    return db.get_calendar_sessions(client_id, session_type)


@st.cache_data(show_spinner=False)
def cached_transactions(ver, month, tx_type=None, category=None) -> pd.DataFrame:
    return db.get_transactions(month, tx_type, category)


@st.cache_data(show_spinner=False)
def cached_sessions(ver, client_id, limit) -> pd.DataFrame:
    return db.get_sessions(client_id, limit)


@st.cache_data(show_spinner=False)
def cached_schedule_slots(ver, from_date=None, to_date=None) -> pd.DataFrame:
    return logic.list_schedule_slots(from_date, to_date)


@st.cache_data(show_spinner=False)
def cached_slot_bookings(ver, slot_id) -> pd.DataFrame:
    return logic.get_slot_bookings(slot_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def client_selector(label: str = "Client", active_only: bool = True,
                    key: str | None = None):
    """Dropdown of clients. Returns (client_id, client_name) or (None, None).
    Pass a unique `key` when calling this more than once on the same page."""
    clients = cached_clients(_ver(), "Active" if active_only else None)
    if clients.empty:
        st.info("No clients yet — add one on the 'Clients & Packages' page.")
        return None, None
    options = {
        f"{row['name']} (#{row['id']})": int(row["id"])
        for _, row in clients.iterrows()
    }
    choice = st.selectbox(label, list(options.keys()), key=key)
    return options[choice], choice


def ils(amount: float) -> str:
    return f"₪{amount:,.0f}"


CLIENT_STATUS_COLORS = {config.CLIENT_STATUS_BADGES["Active"]: COLOR_GOOD,
                        config.CLIENT_STATUS_BADGES["Inactive"]: COLOR_TEXT_MUTED}
PAYMENT_STATUS_COLORS = {config.PAYMENT_STATUSES["Paid"]: COLOR_GOOD,
                         config.PAYMENT_STATUSES["Partial"]: COLOR_WARNING,
                         config.PAYMENT_STATUSES["Pending"]: COLOR_CRITICAL}
TRANSACTION_TYPE_COLORS = {"Income": COLOR_BLUE, "Expense": COLOR_ORANGE}


def _style_status_column(df: pd.DataFrame, column: str, color_map: dict[str, str]):
    """A pandas Styler coloring `column`'s cells by value — the
    dataframe-safe replacement for the old emoji-dot status badges, since
    st.dataframe can't render arbitrary HTML per cell but does render a
    Styler's computed background/text color natively."""
    def _cell(val):
        color = color_map.get(val)
        return (f"background-color:{color}26; color:{color}; font-weight:700;"
               if color else "")
    return df.style.map(_cell, subset=[column])


def session_status_label(row) -> str:
    """Badge text for a session row: flags an Overdraft (חריגה) session in
    red, a normal deduction in green, else a neutral 'no deduction' note."""
    if logic.OVERDRAFT_TAG in (row.get("notes") or ""):
        return "Overdraft / חריגה"
    if row.get("sessions_deducted"):
        return "Deducted"
    return "— No deduction"


def payment_fields(key_prefix: str):
    """Payment status + collected-amount inputs shared by all assignment
    forms. Returns (status_key, entered_amount)."""
    status_label = st.selectbox("Payment status / סטטוס תשלום",
                                list(config.PAYMENT_STATUSES.values()),
                                key=f"{key_prefix}_status")
    status_key = [k for k, v in config.PAYMENT_STATUSES.items()
                  if v == status_label][0]
    entered = st.number_input(
        "Amount collected so far (ILS)", min_value=0.0, step=50.0,
        key=f"{key_prefix}_collected",
        help="Only used for 'שולם חלקית (Partial)'. 'Paid' collects the full "
             "price automatically; 'Pending' collects nothing.")
    return status_key, entered


# ---------------------------------------------------------------------------
# Page 1 — Clients & Packages
# ---------------------------------------------------------------------------
def page_clients():
    st.title("Clients & Packages")

    tab_new, tab_assign, tab_view = st.tabs(
        ["New Client", "Assign Package", "Client List & Balances"]
    )

    # --- New client form -----------------------------------------------------
    # One-stop entry: register the client and (optionally) assign their first
    # package and record the payment as income, all in a single submit.
    with tab_new:
        pack_choices = {"— No package yet —": None}
        for p in config.FRONTAL_PACKAGES.values():
            pack_choices[f"Frontal: {p['label']} — {ils(p['price'])}"] = (
                "pack", p)
        for m in config.MONTHLY_MEMBERSHIPS.values():
            pack_choices[
                f"Membership: {m['label']} — {ils(m['monthly_rate'])}/month"] = (
                "member", m)
        for s in config.ONLINE_SUBSCRIPTIONS.values():
            pack_choices[f"Online: {s['label']} — {ils(s['monthly_rate'])}/month"] = (
                "sub", s)

        with st.form("new_client", clear_on_submit=True):
            name = st.text_input("Full name *")
            phone = st.text_input("Phone")
            service_type = st.selectbox(
                "Service type", config.SERVICE_TYPES,
                help="Set automatically if you pick a package below.")
            st.divider()
            pack_choice = st.selectbox(
                "Assign package now (optional)", list(pack_choices.keys()))
            start = st.date_input("Package start date", value=date.today())
            pay_method = st.selectbox("Payment method", config.PAYMENT_METHODS)
            status_key, entered_amount = payment_fields("newclient")
            record_income = st.checkbox(
                "Record the collected amount as income", value=True)
            submitted = st.form_submit_button("Add Client")

        if submitted:
            try:
                chosen = pack_choices[pack_choice]
                if chosen:
                    service_type = (config.SERVICE_ONLINE
                                    if chosen[0] == "sub"
                                    else config.SERVICE_FRONTAL)
                client_id = logic.create_client(name, phone, service_type)
                msg = f"Client '{name.strip()}' added (#{client_id})."
                if chosen:
                    msg += " " + logic.assign_package(
                        client_id, chosen[0], chosen[1], start, status_key,
                        entered_amount, pay_method, record_income)
                bump_data()
                st.success(msg)
            except logic.ValidationError as e:
                st.error(str(e))
            except Exception as e:
                st.error(f"Could not save the client: {e}")

    # --- Assign package / subscription --------------------------------------
    with tab_assign:
        client_id, _ = client_selector("Assign to client")
        if client_id:
            kind = st.radio(
                "Package kind",
                ["Frontal Training (session pack)",
                 "Monthly Membership (strength + mobility)",
                 "Online Coaching (subscription)"],
                horizontal=True,
            )
            start = st.date_input("Start date", value=date.today())

            if kind.startswith("Monthly"):
                member_options = {
                    f"{m['label']} — {m['sessions']} sessions, "
                    f"{ils(m['monthly_rate'])}/month": m
                    for m in config.MONTHLY_MEMBERSHIPS.values()
                }
                choice = st.selectbox("Membership", list(member_options.keys()))
                member = member_options[choice]
                status_key, entered = payment_fields("assign_member")
                record_income = st.checkbox(
                    "Record the collected amount as income", value=True)
                pay_method = st.selectbox("Payment method", config.PAYMENT_METHODS)
                if st.button("Assign Membership"):
                    try:
                        msg = logic.assign_package(
                            client_id, "member", member, start, status_key,
                            entered, pay_method, record_income)
                        bump_data()
                        st.success(msg)
                    except logic.ValidationError as e:
                        st.error(str(e))
                    except Exception as e:
                        st.error(f"Could not assign the membership: {e}")

            elif kind.startswith("Frontal"):
                pack_options = {
                    f"{p['label']} — {p['sessions']} session(s), {ils(p['price'])}": p
                    for p in config.FRONTAL_PACKAGES.values()
                }
                choice = st.selectbox("Package", list(pack_options.keys()))
                pack = pack_options[choice]
                status_key, entered = payment_fields("assign_pack")
                record_income = st.checkbox(
                    "Record the collected amount as income", value=True)
                pay_method = st.selectbox("Payment method", config.PAYMENT_METHODS)
                if st.button("Assign Package"):
                    try:
                        msg = logic.assign_package(
                            client_id, "pack", pack, start, status_key,
                            entered, pay_method, record_income)
                        bump_data()
                        st.success(msg)
                    except logic.ValidationError as e:
                        st.error(str(e))
                    except Exception as e:
                        st.error(f"Could not assign the package: {e}")

            else:
                sub_options = {
                    f"{s['label']} — {ils(s['monthly_rate'])}/month": s
                    for s in config.ONLINE_SUBSCRIPTIONS.values()
                }
                choice = st.selectbox("Subscription", list(sub_options.keys()))
                sub = sub_options[choice]
                st.caption(
                    f"Minimum commitment: {config.ONLINE_MIN_COMMITMENT_MONTHS} months "
                    f"(total ≥ {ils(sub['monthly_rate'] * config.ONLINE_MIN_COMMITMENT_MONTHS)})"
                )
                status_key, entered = payment_fields("assign_sub")
                record_income = st.checkbox(
                    "Record the collected amount as income", value=True)
                pay_method = st.selectbox("Payment method", config.PAYMENT_METHODS)
                if st.button("Assign Subscription"):
                    try:
                        msg = logic.assign_package(
                            client_id, "sub", sub, start, status_key,
                            entered, pay_method, record_income)
                        bump_data()
                        st.success(msg)
                    except logic.ValidationError as e:
                        st.error(str(e))
                    except Exception as e:
                        st.error(f"Could not assign the subscription: {e}")

    # --- Client list ---------------------------------------------------------
    with tab_view:
        show_inactive = st.checkbox("Show inactive clients")
        clients = cached_clients(_ver(), None if show_inactive else "Active")
        if clients.empty:
            st.info("No clients to show.")
        else:
            packages = db.get_packages()
            # Remaining sessions per client (sum across their session packs)
            if not packages.empty:
                remaining = (
                    packages.dropna(subset=["remaining_sessions"])
                    .groupby("client_id")["remaining_sessions"].sum()
                )
                subs = (
                    packages[packages["monthly_rate"].notna()]
                    .groupby("client_id")["package_type"]
                    .apply(lambda s: ", ".join(s.unique()))
                )
            else:
                remaining, subs = pd.Series(dtype=float), pd.Series(dtype=str)

            view = clients.copy()
            view["remaining_sessions"] = (
                view["id"].map(remaining).fillna(0).astype(int)
            )
            view["subscriptions"] = view["id"].map(subs).fillna("—")
            view["status"] = view["status"].map(
                config.CLIENT_STATUS_BADGES).fillna(view["status"])
            table = view[["id", "name", "phone", "service_type", "status",
                          "remaining_sessions", "subscriptions", "created_at"]]
            st.dataframe(
                _style_status_column(table, "status", CLIENT_STATUS_COLORS),
                use_container_width=True,
                hide_index=True,
            )

            # Quick Contact — contextual WhatsApp actions for one client
            st.divider()
            st.subheader("Quick Contact / יצירת קשר מהירה")
            contact_id, _ = client_selector(
                "Client to contact", active_only=False, key="contact_client")
            if contact_id:
                crow = db.fetch_client(contact_id)
                pkgs = db.get_packages(contact_id)
                has_debt = not pkgs.empty and bool((
                    (pkgs["price"].fillna(0) - pkgs["amount_paid"].fillna(0))
                    > 0).any())
                active_pack = db.get_active_session_package(contact_id)
                is_low = (active_pack is not None
                         and active_pack["remaining_sessions"] <= 2)
                is_inactive = contact_id in set(
                    logic.detect_inactive_clients()["id"])

                wa_col1, wa_col2, wa_col3 = st.columns(3)
                with wa_col1:
                    if has_debt:
                        link = logic.whatsapp_link(
                            crow["phone"], "payment_reminder",
                            name=crow["name"])
                        whatsapp_button("Payment Reminder", link)
                    else:
                        st.caption("No open balance")
                with wa_col2:
                    if is_low:
                        link = logic.whatsapp_link(
                            crow["phone"], "low_sessions", name=crow["name"])
                        whatsapp_button("Low Sessions Alert", link)
                    else:
                        st.caption("— Balance OK")
                with wa_col3:
                    if is_inactive:
                        link = logic.whatsapp_link(
                            crow["phone"], "inactivity", name=crow["name"])
                        whatsapp_button("Re-engage", link)
                    else:
                        st.caption("— Active recently")

            # Toggle client status
            st.divider()
            st.subheader("Change client status")
            col1, col2 = st.columns([3, 1])
            with col1:
                target_id, _ = client_selector("Client", active_only=False,
                                               key="status_client")
            with col2:
                new_status = st.selectbox("Status", ["Active", "Inactive"])
            if st.button("Update Status") and target_id:
                db.set_client_status(target_id, new_status)
                bump_data()
                st.success("Status updated.")
                st.rerun()


# ---------------------------------------------------------------------------
# Page 2 — Log & Calendar
# ---------------------------------------------------------------------------
def render_client_stats(client_id: int):
    """Quick stats summary panel for the selected client."""
    stats = logic.client_stats(client_id)
    pack, sub = stats["active_pack"], stats["subscription"]

    c1, c2, c3 = st.columns(3)
    c1.metric("Total Sessions Completed", stats["total_sessions"])
    if pack:
        c2.metric("Active Package", pack["package_type"])
        c3.metric("Remaining Sessions", pack["remaining_sessions"])
        used = pack["total_sessions"] - pack["remaining_sessions"]
        st.progress(
            used / pack["total_sessions"],
            text=f"{used}/{pack['total_sessions']} sessions completed "
                 f"({used / pack['total_sessions']:.0%})",
        )
    elif sub:
        c2.metric("Active Subscription", sub["package_type"])
        c3.metric("Monthly Rate", ils(sub["monthly_rate"]))
        st.caption("Subscription client — sessions are logged without "
                   "deduction unless a pack is assigned.")
    else:
        c2.metric("Active Package", "—")
        c3.metric("Remaining Sessions", "—")
        st.warning(
            "This client has no session pack with remaining sessions. "
            "The session will be logged without deduction."
        )
    # A membership row has both a session quota and a monthly rate — if the
    # client also has a separate subscription, surface it too.
    if pack and sub and sub["id"] != pack["id"]:
        st.caption(f"Also subscribed: {sub['package_type']} "
                   f"({ils(sub['monthly_rate'])}/month)")

    # --- Per-type session breakdown -----------------------------------------
    type_counts = logic.session_type_counts(client_id)
    if type_counts:
        parts = [f"{config.SESSION_TYPES.get(t, 'ללא סוג')}: **{n}**"
                 for t, n in sorted(type_counts.items(),
                                    key=lambda kv: -kv[1]) if t or n]
        st.markdown("Session breakdown (all-time): " + " • ".join(parts))

    # Hybrid membership (e.g. 8 Strength + 4 Mobility): completed vs
    # remaining per type, counted since the membership started.
    if pack:
        membership = next(
            (m for m in config.MONTHLY_MEMBERSHIPS.values()
             if m["label"] == pack["package_type"]), None)
        if membership and "quota" in membership:
            since_counts = logic.session_type_counts(
                client_id, since=pack["start_date"])
            quota_cols = st.columns(len(membership["quota"]))
            for col, (stype, quota) in zip(quota_cols,
                                           membership["quota"].items()):
                done = since_counts.get(stype, 0)
                with col:
                    st.progress(
                        min(done / quota, 1.0),
                        text=f"{config.SESSION_TYPES[stype]}: "
                             f"{done}/{quota} completed"
                             f" ({max(quota - done, 0)} remaining)")


def render_recent_sessions(client_id: int):
    """Recent session history for a client, with per-row delete buttons."""
    st.subheader("Recent sessions")

    # Success message stashed by a delete click on the previous run
    deleted_msg = st.session_state.pop("session_deleted_msg", None)
    if deleted_msg:
        st.success(deleted_msg)

    recent = cached_sessions(_ver(), client_id, 20)
    if recent.empty:
        st.info("No sessions logged for this client yet.")
    else:
        crow = db.fetch_client(client_id)
        active_pack = db.get_active_session_package(client_id)
        current_remaining = (active_pack["remaining_sessions"]
                             if active_pack else None)
        for _, row in recent.iterrows():
            col_date, col_type, col_notes, col_ded, col_wa, col_del = (
                st.columns([2, 2, 3, 2, 2, 1]))
            col_date.write(row["session_date"])
            col_type.write(config.SESSION_TYPES.get(row["session_type"], "—"))
            col_type.caption(config.TRAINERS.get(row["trainer"], "—"))
            display_notes = (row["notes"] or "").replace(
                f"{logic.OVERDRAFT_TAG} | ", "").replace(
                logic.OVERDRAFT_TAG, "").strip(" |") or "—"
            col_notes.write(display_notes)
            col_ded.markdown(session_status_label(row))
            with col_wa:
                if current_remaining is not None:
                    wa_link = logic.whatsapp_link(
                        crow["phone"], "session_confirmation",
                        name=crow["name"],
                        session_type=SESSION_TYPE_SHORT.get(
                            row["session_type"], row["session_type"] or ""),
                        remaining=current_remaining)
                    whatsapp_button("Confirm", wa_link)
                else:
                    st.caption("—")
            if col_del.button("X", key=f"del_sess_{row['id']}",
                              help="Delete this session log"):
                result = logic.delete_session(int(row["id"]))
                if result["restored"]:
                    msg = (f"Session on {row['session_date']} deleted — "
                           f"1 session restored to {row['client_name']}'s "
                           f"{result['package_type']} "
                           f"(**{result['remaining']}** remaining).")
                elif row["sessions_deducted"]:
                    msg = (f"Session on {row['session_date']} deleted. "
                           "No pack with used sessions was found, so no "
                           "balance was restored.")
                else:
                    msg = (f"Session on {row['session_date']} deleted "
                           "(it had no deduction, balance unchanged).")
                st.session_state["session_deleted_msg"] = msg
                bump_data()
                st.rerun()


def page_log_calendar():
    """Unified page: log-session form (left) + interactive calendar (right)
    + recent session history (below).

    Code order matters for real-time updates: the form submission is
    processed FIRST, so the stats panel, calendar and history — all rendered
    afterwards in the same run — already reflect the new session. Deletes use
    st.rerun(), so they refresh everything too."""
    st.title("Log & Calendar — תיעוד ויומן אימונים")

    col_form, col_cal = st.columns([2, 3], gap="large")

    client_id = None
    with col_form:
        client_id, _ = client_selector("Active client")
        if client_id:
            # Placeholder slot: stats are rendered into it AFTER the form is
            # processed, so a just-logged session is already in the numbers.
            stats_box = st.container()
            pack_exists = db.get_active_session_package(client_id) is not None

            with st.form("log_session", clear_on_submit=True):
                session_date = st.date_input("Session date", value=date.today())
                type_label = st.radio("Session type *",
                                      list(config.SESSION_TYPES.values()),
                                      horizontal=True)
                trainer_label = st.radio(
                    "Trainer / מאמן *", list(config.TRAINERS.values()),
                    horizontal=True,
                    help="Drives the revenue split for this session's "
                         "package (70% conducting trainer / 10% partner / "
                         "20% savings).")
                notes = st.text_area(
                    "Notes (optional)",
                    placeholder="e.g. Lower body strength, PR on squat")
                deduct = st.checkbox("Deduct 1 session from pack",
                                     value=pack_exists)
                submitted = st.form_submit_button("Log Completed Session")

            if submitted:
                try:
                    session_type = [k for k, v in config.SESSION_TYPES.items()
                                    if v == type_label][0]
                    trainer = [k for k, v in config.TRAINERS.items()
                              if v == trainer_label][0]
                    result = logic.log_session(client_id, session_date, notes,
                                               deduct=deduct,
                                               session_type=session_type,
                                               trainer=trainer)
                    bump_data()
                    if result["deducted"]:
                        st.success(
                            f"Session logged. 1 session deducted — "
                            f"**{result['remaining']}** remaining."
                        )
                        if result["remaining"] == 0:
                            st.warning("Pack finished! "
                                       "Time to offer a renewal.")
                        crow = db.fetch_client(client_id)
                        wa_link = logic.whatsapp_link(
                            crow["phone"], "session_confirmation",
                            name=crow["name"],
                            session_type=SESSION_TYPE_SHORT.get(
                                session_type, session_type),
                            remaining=result["remaining"])
                        whatsapp_button("Send Confirmation", wa_link)
                    elif result["overdraft"]:
                        st.warning(
                            "Session logged as **Overdraft / חריגה** — "
                            "the client's packs have 0 sessions left. "
                            "Consider assigning a new pack or collecting "
                            "payment.")
                    else:
                        st.success("Session logged (no deduction).")
                except logic.ValidationError as e:
                    st.error(str(e))
                except Exception as e:
                    st.error(f"Could not log the session: {e}")

            with stats_box:
                render_client_stats(client_id)

    with col_cal:
        render_calendar()

    if client_id:
        st.divider()
        render_recent_sessions(client_id)


# ---------------------------------------------------------------------------
# Calendar component (rendered inside the Log & Calendar page)
# ---------------------------------------------------------------------------
# Event colors reuse the app-wide SESSION_TYPE_COLORS (design system, above)
# so the calendar, the dashboard donut and any future chart always agree on
# what each session type's color means.
CALENDAR_UNTYPED_COLOR = CHART_MUTED_INK

# Short Hebrew tag shown inside calendar entries
SESSION_TYPE_SHORT = {
    "Strength": "כוח",
    "Mobility": "מוביליטי",
    "Athletics": "אתלטיקה",
}


def render_calendar():
    st.subheader("יומן אימונים")

    # --- Filters ------------------------------------------------------------
    col_client, col_type = st.columns(2)
    with col_client:
        clients = cached_clients(_ver())  # active + inactive both appear in history
        client_map = {"All clients": None}
        client_map.update({
            f"{row['name']} (#{row['id']})": int(row["id"])
            for _, row in clients.iterrows()
        })
        client_choice = st.selectbox("Filter by client", list(client_map.keys()))
    with col_type:
        type_map = {"All types": None}
        type_map.update({v: k for k, v in config.SESSION_TYPES.items()})
        type_choice = st.selectbox("Filter by session type",
                                   list(type_map.keys()))

    sessions = cached_calendar_sessions(_ver(),
        client_id=client_map[client_choice],
        session_type=type_map[type_choice],
    )

    if sessions.empty:
        st.info("No sessions match the current filters — log a session and "
                "it will appear here immediately.")
        return

    # --- Build calendar events ---------------------------------------------
    events = []
    for _, row in sessions.iterrows():
        title = row["client_name"]
        if row["session_type"]:
            title += f" · {SESSION_TYPE_SHORT.get(row['session_type'], '')}"
        if row["notes"]:
            title += f" — {row['notes'][:40]}"
        events.append({
            "id": str(row["id"]),
            "title": title,
            "start": row["session_date"],
            "allDay": True,
            "color": SESSION_TYPE_COLORS.get(row["session_type"],
                                         CALENDAR_UNTYPED_COLOR),
        })

    options = {
        "initialView": "dayGridMonth",
        "initialDate": sessions["session_date"].max(),
        "headerToolbar": {
            "left": "prev,next today",
            "center": "title",
            "right": "dayGridMonth,dayGridWeek,listMonth",
        },
        "firstDay": 0,  # week starts Sunday
        "height": 560,
        "dayMaxEventRows": 4,
    }

    # The component caches by key — derive the key from the data so the
    # calendar re-renders immediately when sessions are logged or deleted.
    cal_key = f"cal_{hash((tuple(sessions['id']), client_choice, type_choice))}"
    st_calendar(events=events, options=options, key=cal_key)

    dot = ('<span style="display:inline-block;width:8px;height:8px;'
          'border-radius:50%;background:{c};margin-inline-end:4px;"></span>')
    st.markdown(
        f"**{len(events)} session(s) shown**&nbsp;&nbsp;•&nbsp;&nbsp;"
        + " ".join(
            dot.format(c=c) + f'{config.SESSION_TYPES[t]}&nbsp;&nbsp;'
            for t, c in SESSION_TYPE_COLORS.items()
        )
        + dot.format(c=CALENDAR_UNTYPED_COLOR) + "ללא סוג",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Page 3 — Finance
# ---------------------------------------------------------------------------
def render_debts_tab():
    """Open balances + a form to record a collected debt payment."""
    debt_msg = st.session_state.pop("debt_msg", None)
    if debt_msg:
        st.success(debt_msg)

    debts = db.get_debts()
    st.metric("Total Unpaid Balance / חובות פתוחים", ils(db.get_total_unpaid()))
    if debts.empty:
        st.success("No open debts — everyone is paid up!")
        return

    view = debts.copy()
    view["payment_status"] = view["payment_status"].map(
        config.PAYMENT_STATUSES).fillna(view["payment_status"])
    table = view[["client_name", "package_type", "price", "amount_paid",
                  "balance", "payment_status", "start_date"]]
    st.dataframe(
        _style_status_column(table, "payment_status", PAYMENT_STATUS_COLORS),
        use_container_width=True, hide_index=True)

    st.subheader("Record a debt payment")
    debt_options = {
        f"{r['client_name']} — {r['package_type']} "
        f"(balance {ils(r['balance'])})": r
        for _, r in debts.iterrows() if r["balance"] > 0
    }
    if not debt_options:
        st.info("No positive balances to collect.")
        return
    choice = st.selectbox("Open balance", list(debt_options.keys()))
    chosen = debt_options[choice]
    col_a, col_b, col_c = st.columns(3)
    with col_a:
        amount = st.number_input("Amount received (ILS)", min_value=0.0,
                                 value=float(chosen["balance"]), step=50.0)
    with col_b:
        pay_method = st.selectbox("Payment method", config.PAYMENT_METHODS)
    with col_c:
        pay_date = st.date_input("Date", value=date.today())
    record_income = st.checkbox("Record as income (Debt Collection)",
                                value=True)
    if st.button("Record Payment") and amount > 0:
        try:
            result = logic.record_debt_payment(
                int(chosen["id"]), amount, pay_method, pay_date,
                record_income)
        except logic.ValidationError as e:
            st.error(str(e))
            st.stop()
        except Exception as e:
            st.error(f"Could not record the payment: {e}")
            st.stop()
        bump_data()
        status_label = config.PAYMENT_STATUSES[result["status"]]
        st.session_state["debt_msg"] = (
            f"{ils(amount)} collected from {chosen['client_name']} — "
            f"status now {status_label}, remaining balance "
            f"{ils(result['balance'])}.")
        st.rerun()


def render_partner_payouts_tab():
    """Monthly revenue-split breakdown between Coach Erez, Coach Yuval and
    the Business Savings Fund, plus manual adjustments."""
    payout_msg = st.session_state.pop("payout_msg", None)
    if payout_msg:
        st.success(payout_msg)

    months = logic.transaction_months()
    current_month = date.today().strftime("%Y-%m")
    default_idx = months.index(current_month) if current_month in months else 0
    month = st.selectbox("Month / חודש", months, index=default_idx,
                         key="payout_month")

    result = logic.compute_monthly_payouts(month)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Coach Erez / מאמן ארז", ils(result["erez_total"]))
    c2.metric("Coach Yuval / מאמן יובל", ils(result["yuval_total"]))
    c3.metric("Business Savings — this month / חיסכון העסק",
             ils(result["savings_total"]))
    c4.metric("Accumulated Savings (all time) / חיסכון מצטבר",
             ils(logic.alltime_accumulated_savings()))

    if result["pending_total"] > 0:
        st.warning(
            f"{ils(result['pending_total'])} in Frontal Training income "
            "is pending trainer assignment (savings are still allocated — "
            "only the Erez/Yuval split is on hold). Log the related "
            "sessions with a trainer, or add a manual adjustment below.")

    st.subheader("How this month's revenue was split")
    if result["rows"]:
        detail = pd.DataFrame(result["rows"])
        detail = detail.rename(columns={
            "date": "Date", "client_name": "Client", "description": "Package",
            "rule": "Rule", "note": "Trainer detail", "gross": "Gross (₪)",
            "erez": "Erez (₪)", "yuval": "Yuval (₪)", "savings": "Savings (₪)",
            "status": "Status",
        })
        st.dataframe(detail, use_container_width=True, hide_index=True)
    else:
        st.info("No income recorded for this month yet.")

    st.divider()
    st.subheader("Manual Payout Adjustment / התאמה ידנית")
    st.caption("Corrects or records payouts the automatic split can't "
               "cover — e.g. a cash tip, a one-off arrangement, or fixing a "
               "past error. Amounts add to the totals above and can be "
               "negative. A reason is required for the audit trail.")
    with st.form("payout_adjustment", clear_on_submit=True):
        a1, a2, a3 = st.columns(3)
        with a1:
            erez_amt = st.number_input("Erez amount (ILS)", step=10.0,
                                       format="%.2f")
        with a2:
            yuval_amt = st.number_input("Yuval amount (ILS)", step=10.0,
                                        format="%.2f")
        with a3:
            savings_amt = st.number_input("Savings amount (ILS)", step=10.0,
                                          format="%.2f")
        reason = st.text_input("Reason / הסבר *")
        submitted = st.form_submit_button("Save Adjustment")

    if submitted:
        try:
            logic.add_payout_adjustment(month, erez_amt, yuval_amt,
                                        savings_amt, reason)
            bump_data()
            st.session_state["payout_msg"] = (
                f"Adjustment saved for {month}: Erez {ils(erez_amt)} / "
                f"Yuval {ils(yuval_amt)} / Savings {ils(savings_amt)}.")
            st.rerun()
        except logic.ValidationError as e:
            st.error(str(e))
        except Exception as e:
            st.error(f"Could not save the adjustment: {e}")

    adjustments = db.get_payout_adjustments(month)
    if not adjustments.empty:
        st.caption(f"Existing adjustments for {month}:")
        for _, row in adjustments.iterrows():
            cols = st.columns([2, 2, 2, 2, 3, 1])
            cols[0].write(ils(row["erez_amount"]))
            cols[1].write(ils(row["yuval_amount"]))
            cols[2].write(ils(row["savings_amount"]))
            cols[3].write(row["created_at"][:16])
            cols[4].write(row["reason"] or "—")
            if cols[5].button("X", key=f"del_adj_{row['id']}",
                              help="Delete this adjustment"):
                db.delete_payout_adjustment(int(row["id"]))
                bump_data()
                st.rerun()


def page_finance():
    st.title("Finance — Income & Expenses")

    tab_add, tab_view, tab_debts, tab_payouts = st.tabs(
        ["Add Transaction", "Transactions", "Debts / חובות",
         "Partner Payouts / חלוקת שכר"])

    with tab_debts:
        render_debts_tab()

    with tab_payouts:
        render_partner_payouts_tab()

    with tab_add:
        tx_type = st.radio("Type", ["Income", "Expense"], horizontal=True)
        categories = (config.INCOME_CATEGORIES if tx_type == "Income"
                      else config.EXPENSE_CATEGORIES)

        with st.form("add_tx", clear_on_submit=True):
            amount = st.number_input("Amount (ILS)", min_value=0.0, step=10.0)
            category = st.selectbox("Category / קטגוריה", categories)
            pay_method = st.selectbox("Payment method / אמצעי תשלום",
                                      config.PAYMENT_METHODS)
            tx_date = st.date_input("Date", value=date.today())
            reference = st.text_input(
                "Reference number / מספר אסמכתא-קבלה (optional)")
            notes = st.text_input("הערות / פירוט (optional)")

            # Optionally link income to a client
            clients = cached_clients(_ver())
            client_map = {"— None —": None}
            client_map.update({
                f"{row['name']} (#{row['id']})": int(row["id"])
                for _, row in clients.iterrows()
            })
            client_choice = st.selectbox("Link to client (optional)",
                                         list(client_map.keys()))
            submitted = st.form_submit_button(f"Add {tx_type}")

        if submitted:
            if amount <= 0:
                st.error("Amount must be greater than 0.")
            else:
                try:
                    db.add_transaction(tx_type, amount, category, pay_method,
                                       tx_date, client_map[client_choice],
                                       notes, reference)
                    bump_data()
                    st.success(f"{tx_type} of {ils(amount)} recorded.")
                except Exception as e:
                    st.error(f"Could not record the transaction: {e}")

    with tab_view:
        fin_msg = st.session_state.pop("fin_edit_msg", None)
        if fin_msg:
            st.success(fin_msg)

        # --- Filters: month / type / category -------------------------------
        f1, f2, f3 = st.columns(3)
        with f1:
            months = ["All time"] + logic.transaction_months()
            month_choice = st.selectbox("Month / חודש", months)
            month = None if month_choice == "All time" else month_choice
        with f2:
            type_choice = st.selectbox("Type", ["All", "Income", "Expense"])
            tx_type_f = None if type_choice == "All" else type_choice
        with f3:
            cat_options = ["All categories"] + logic.transaction_categories()
            cat_choice = st.selectbox("Category / קטגוריה", cat_options)
            category_f = (None if cat_choice == "All categories"
                          else cat_choice)

        # --- P&L card for the selected month --------------------------------
        render_pnl(month)

        tx = cached_transactions(_ver(), month, tx_type_f, category_f)
        if tx.empty:
            st.info("No transactions match the current filters.")
        else:
            tx_table = tx[["date", "type", "amount", "category", "payment_method",
                          "reference_number", "client_name", "notes"]]
            st.dataframe(
                _style_status_column(tx_table, "type", TRANSACTION_TYPE_COLORS),
                use_container_width=True, hide_index=True)
            with st.expander("Edit / delete these transactions"):
                _transactions_editor(tx, key="fin", msg_key="fin_edit_msg")


# ---------------------------------------------------------------------------
# Page 4 — Overview Dashboard (with quick-edit mode)
# ---------------------------------------------------------------------------
def _packages_editor():
    st.caption("Fix session counts, override monthly rates or start dates. "
               "Client and package names are read-only.")
    pkgs = db.get_packages()
    if pkgs.empty:
        st.info("No packages to edit.")
        return
    view = pkgs[["id", "client_name", "package_type", "total_sessions",
                 "remaining_sessions", "monthly_rate", "start_date",
                 "price", "amount_paid", "payment_status"]].copy()
    view["start_date"] = pd.to_datetime(view["start_date"])
    edited = st.data_editor(
        view, hide_index=True, key="pkg_editor",
        disabled=["id", "client_name", "package_type"],
        column_config={
            "total_sessions": st.column_config.NumberColumn(
                "total_sessions", min_value=0, step=1),
            "remaining_sessions": st.column_config.NumberColumn(
                "remaining_sessions", min_value=0, step=1),
            "monthly_rate": st.column_config.NumberColumn(
                "monthly_rate (ILS)", min_value=0.0, format="%.0f"),
            "start_date": st.column_config.DateColumn("start_date"),
            "price": st.column_config.NumberColumn(
                "price (ILS)", min_value=0.0, format="%.0f"),
            "amount_paid": st.column_config.NumberColumn(
                "amount_paid (ILS)", min_value=0.0, format="%.0f"),
            "payment_status": st.column_config.SelectboxColumn(
                "payment_status", options=list(config.PAYMENT_STATUSES)),
        },
        use_container_width=True,
    )
    if st.button("Save package changes"):
        n, errors = logic.apply_package_edits(pkgs, edited)
        bump_data()
        for e in errors:
            st.error(e)
        if n:
            st.session_state["dash_edit_msg"] = (
                f"{n} package record(s) updated — balances and dashboard "
                "metrics recalculated.")
            st.rerun()
        elif not errors:
            st.info("No changes to save.")


def _transactions_editor(tx: pd.DataFrame, key: str = "tx",
                         msg_key: str = "dash_edit_msg"):
    """Reusable inline transaction editor. `tx` is the (possibly filtered)
    dataframe to edit; `key` namespaces the widget, `msg_key` is the
    session_state slot the caller reads its confirmation message from."""
    st.caption("Edit amounts, categories, dates, reference numbers or notes. "
               "Delete a row with the row checkbox + Delete, or add one with "
               "the add-row button. Changes apply when you press Save.")
    if tx.empty:
        st.info("No transactions to edit.")
        return
    view = tx[["id", "type", "amount", "category", "payment_method",
               "date", "reference_number", "client_name", "notes"]].copy()
    view["date"] = pd.to_datetime(view["date"])
    cats = sorted(set(config.INCOME_CATEGORIES + config.EXPENSE_CATEGORIES)
                  | set(view["category"].dropna()))
    pays = sorted(set(config.PAYMENT_METHODS)
                  | set(view["payment_method"].dropna()))
    edited = st.data_editor(
        view, hide_index=True, key=f"{key}_editor", num_rows="dynamic",
        disabled=["id", "client_name"],
        column_config={
            "type": st.column_config.SelectboxColumn(
                "type", options=["Income", "Expense"], required=True),
            "amount": st.column_config.NumberColumn(
                "amount (ILS)", min_value=0.0, format="%.2f"),
            "category": st.column_config.SelectboxColumn(
                "category", options=cats),
            "payment_method": st.column_config.SelectboxColumn(
                "payment_method", options=pays),
            "date": st.column_config.DateColumn("date"),
            "reference_number": st.column_config.TextColumn(
                "reference_number / אסמכתא"),
            "notes": st.column_config.TextColumn("notes / הערות"),
        },
        use_container_width=True,
    )
    if st.button("Save transaction changes", key=f"{key}_save"):
        n_upd, n_del, n_add, errors = logic.apply_transaction_edits(tx, edited)
        bump_data()
        for e in errors:
            st.error(e)
        if n_upd or n_del or n_add:
            parts = []
            if n_upd:
                parts.append(f"{n_upd} updated")
            if n_del:
                parts.append(f"{n_del} deleted")
            if n_add:
                parts.append(f"{n_add} added")
            st.session_state[msg_key] = (
                f"Transactions saved ({', '.join(parts)}) — dashboard "
                "totals recalculated.")
            st.rerun()
        elif not errors:
            st.info("No changes to save.")


def render_pnl(month: str | None):
    """Profit & Loss card for the given 'YYYY-MM' month (None = all time)."""
    result = logic.pnl(month)
    label = "All time" if month is None else month
    st.markdown(f"**P&L — {label}**")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Income", ils(result["income"]))
    c2.metric("Total Expenses", ils(result["expense"]))
    c3.metric("Net Profit", ils(result["net"]))
    margin_txt = f"{result['margin']:.1f}%" if result["margin"] is not None \
        else "—"
    c4.metric("Profit Margin %", margin_txt)


def page_dashboard():
    st.title("Overview Dashboard")

    # Confirmation from an edit applied on the previous run
    edit_msg = st.session_state.pop("dash_edit_msg", None)
    if edit_msg:
        st.success(edit_msg)

    stats = logic.dashboard_stats()

    st.subheader(stats["month_label"])
    kpi_row([
        {"label": "Active Clients", "accent": "blue",
         "value": str(stats["active_clients"])},
        {"label": "Sessions This Month", "accent": "aqua",
         "value": str(stats["sessions_this_month"])},
        {"label": "Income This Month", "accent": "blue",
         "value": ils(stats["finance"]["income"])},
        {"label": "Net This Month", "accent": "good",
         "value": ils(stats["finance"]["net"])},
        {"label": "Total Unpaid / חובות פתוחים",
         "accent": "critical" if stats["total_unpaid"] else "good",
         "value": ils(stats["total_unpaid"])},
    ])

    # --- P&L widget: defaults to the current month, any month selectable ---
    st.divider()
    months = logic.transaction_months()
    current_month = date.today().strftime("%Y-%m")
    default_idx = months.index(current_month) if current_month in months else 0
    pnl_month = st.selectbox("P&L month / חודש לדוח רווח והפסד",
                             months, index=default_idx, key="dash_pnl_month")
    render_pnl(pnl_month)

    # --- Charts: session types · revenue split · income vs expenses --------
    st.divider()
    chart_col1, chart_col2, chart_col3 = st.columns(3)

    with chart_col1:
        type_counts = logic.session_type_distribution(pnl_month)
        if type_counts:
            labels = [config.SESSION_TYPES[k] for k in type_counts]
            values = list(type_counts.values())
            colors = [SESSION_TYPE_COLORS[k] for k in type_counts]
            st.plotly_chart(
                donut_chart(labels, values, colors,
                           title="Session Types / סוגי אימונים", unit="sessions",
                           center_text=str(sum(values))),
                use_container_width=True, config={"displayModeBar": False})
        else:
            st.info("No sessions logged this month yet.")

    with chart_col2:
        payout = logic.compute_monthly_payouts(pnl_month)
        payout_values = {"Erez": payout["erez_total"], "Yuval": payout["yuval_total"],
                         "Savings": payout["savings_total"]}
        if sum(payout_values.values()) > 0:
            labels = ["מאמן ארז (Erez)", "מאמן יובל (Yuval)", "חיסכון (Savings)"]
            st.plotly_chart(
                donut_chart(labels, list(payout_values.values()),
                           [PAYOUT_COLORS[k] for k in payout_values],
                           title="Revenue Split / חלוקת הכנסות",
                           center_text=ils(sum(payout_values.values()))),
                use_container_width=True, config={"displayModeBar": False})
        else:
            st.info("No income to split this month yet.")

    with chart_col3:
        series = logic.monthly_income_expense_series(6)
        if not series.empty:
            st.plotly_chart(income_expense_bar_chart(series),
                            use_container_width=True,
                            config={"displayModeBar": False})
        else:
            st.info("No financial history yet.")

    # --- Requires Attention: debts · low sessions · inactivity -------------
    st.divider()
    st.subheader("Requires Attention / דורש טיפול")
    inactive_clients = logic.detect_inactive_clients()

    tab_debts, tab_low, tab_inactive = st.tabs([
        f"Pending Payments ({len(stats['debts'])})",
        f"Low Sessions ({len(stats['low_packs'])})",
        f"Inactivity ({len(inactive_clients)})",
    ])

    with tab_debts:
        if stats["debts"].empty:
            st.success("No open debts — everyone is paid up!")
        else:
            for _, row in stats["debts"].iterrows():
                c1, c2, c3 = st.columns([3, 3, 2])
                c1.markdown(f"**{row['client_name']}** — {row['package_type']}")
                status_label = config.PAYMENT_STATUSES.get(
                    row["payment_status"], row["payment_status"])
                c2.caption(f"Balance: {ils(row['balance'])} · {status_label}")
                with c3:
                    link = logic.whatsapp_link(
                        row["phone"], "payment_reminder",
                        name=row["client_name"])
                    whatsapp_button("Remind", link)
            st.caption("Collect payments in Finance → Debts / חובות.")

    with tab_low:
        if stats["low_packs"].empty:
            st.success("No clients are running low — all good!")
        else:
            for _, row in stats["low_packs"].iterrows():
                c1, c2, c3 = st.columns([3, 3, 2])
                c1.markdown(f"**{row['client_name']}** — {row['package_type']}")
                c2.caption(f"{row['remaining_sessions']} session(s) left")
                with c3:
                    link = logic.whatsapp_link(
                        row["phone"], "low_sessions", name=row["client_name"])
                    whatsapp_button("Alert", link)

    with tab_inactive:
        st.caption(
            f"Active clients with no logged session in "
            f"{config.INACTIVITY_THRESHOLD_DAYS}+ days.")
        if inactive_clients.empty:
            st.success("No inactive clients — everyone's engaged!")
        else:
            for _, row in inactive_clients.iterrows():
                c1, c2, c3 = st.columns([3, 3, 2])
                c1.markdown(f"**{row['name']}**")
                c2.caption(f"{int(row['days_inactive'])} days since last "
                          f"session (last: {row['last_session_date']})")
                with c3:
                    link = logic.whatsapp_link(
                        row["phone"], "inactivity", name=row["name"])
                    whatsapp_button("Re-engage", link)

    # --- Quick-edit mode ----------------------------------------------------
    st.divider()
    edit_mode = st.toggle(
        "Edit Dashboard Data",
        help="Inline-edit package balances, subscription rates/dates and "
             "financial transactions. Changes are saved to the database and "
             "the metrics above update immediately.")
    if edit_mode:
        tab_pkg, tab_fin = st.tabs(
            ["Packages & Session Balances", "Financial Transactions"])
        with tab_pkg:
            _packages_editor()
        with tab_fin:
            _transactions_editor(cached_transactions(_ver(), None),
                                 key="dash", msg_key="dash_edit_msg")

    st.divider()
    st.subheader("Latest sessions")
    recent = cached_sessions(_ver(), None, 10)
    if recent.empty:
        st.info("No sessions logged yet.")
    else:
        recent = recent.copy()
        recent["status"] = recent.apply(session_status_label, axis=1)
        st.dataframe(
            recent[["session_date", "client_name", "session_type",
                    "trainer", "status", "notes"]],
            use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# Page 3 — Schedule / מערכת שעות
# ---------------------------------------------------------------------------
def _slot_seats_badge(available: int) -> str:
    if available <= 0:
        return '<span class="gug-schedule-seats gug-schedule-seats-full">מלא (Full)</span>'
    return (f'<span class="gug-schedule-seats gug-schedule-seats-open">'
           f'נותרו {available} מקומות</span>')


def _slot_card_html(row, registered: bool = False) -> str:
    """Shared slot-block markup for both the public and admin weekly grids
    — color-coded by session type (SESSION_TYPE_COLORS, the same palette
    already used by the charts/calendar) for quick visual scanning.
    `registered` highlights a slot the identified client is already booked
    into (public view only — admin call sites never pass it)."""
    type_label = config.SESSION_TYPES.get(row["session_type"],
                                          row["session_type"] or "—")
    coach_label = config.TRAINERS.get(row["coach_name"], row["coach_name"])
    color = SESSION_TYPE_COLORS.get(row["session_type"], "#898781")
    available = int(row["available_seats"])
    css_class = "gug-slot-block gug-slot-block-registered" if registered else "gug-slot-block"
    badge = ('<span class="gug-schedule-seats" style="background:rgba(34,197,94,.18);'
            'color:#22C55E;">רשומ/ה</span>' if registered else _slot_seats_badge(available))
    return (
        f'<div class="{css_class}" style="border-inline-start-color:{color};">'
        f'<div class="gug-slot-time">{row["start_time"]}–{row["end_time"]}</div>'
        f'<div class="gug-slot-type" style="color:{color};">{type_label}</div>'
        f'<div class="gug-slot-coach">{coach_label}</div>'
        f'{badge}'
        f'</div>'
    )


def _week_nav(key_prefix: str, max_offset: int, min_offset: int = 0) -> int:
    """Prev/Next week navigation shared by the public and admin weekly
    grids. Returns the currently-selected week offset (0 = the 7 days
    starting today), persisted in st.session_state."""
    state_key = f"{key_prefix}_week_offset"
    offset = max(min_offset, min(max_offset, st.session_state.get(state_key, 0)))
    nav1, nav2, nav3 = st.columns([1, 4, 1])
    with nav1:
        if st.button("◀", key=f"{key_prefix}_prev_week",
                     disabled=offset <= min_offset, width="stretch"):
            st.session_state[state_key] = offset - 1
            st.rerun()
    with nav3:
        if st.button("▶", key=f"{key_prefix}_next_week",
                     disabled=offset >= max_offset, width="stretch"):
            st.session_state[state_key] = offset + 1
            st.rerun()
    start = date.today() + timedelta(days=offset * 7)
    end = start + timedelta(days=6)
    with nav2:
        st.markdown(f'<div class="gug-week-nav-range">{start.strftime("%d/%m")} '
                   f'– {end.strftime("%d/%m")}</div>', unsafe_allow_html=True)
    return offset


def _week_days(offset: int) -> list:
    start = date.today() + timedelta(days=offset * 7)
    return [start + timedelta(days=i) for i in range(7)]


def render_public_schedule_view():
    st.subheader("לוח האימונים השבועי / Weekly Schedule")
    st.caption("זהו את עצמכם והירשמו ישירות מהלוח, או בקשו הרשמה ב-WhatsApp.")

    # --- Step 1: simple client self-identification (phone only — never a
    # name dropdown, which would require listing every client publicly). ---
    st.markdown("**זיהוי מתאמן/ת / Identify yourself (optional)**")
    phone_entry = st.text_input(
        "כבר יש לך כרטיסייה? הזן/י את מספר הטלפון שלך כדי להירשם ישירות",
        key="public_phone_lookup", placeholder="050-1234567")

    lookup = None
    can_book = False
    if phone_entry.strip():
        lookup = logic.find_client_by_phone(phone_entry)
        can_book = bool(lookup and lookup["can_book"])
        if lookup and can_book:
            if lookup["package_kind"] == "pack":
                st.success(
                    f"שלום {lookup['name']}! נותרו לך "
                    f"**{lookup['remaining_sessions']}** אימונים בכרטיסייה — "
                    "בחרו אימון בלוח למטה והירשמו ישירות.")
            else:
                st.success(
                    f"שלום {lookup['name']}! המנוי שלך פעיל — "
                    "בחרו אימון בלוח למטה והירשמו ישירות.")

            pack_value = (str(lookup["remaining_sessions"])
                         if lookup["package_kind"] == "pack" else "פעיל")
            pack_sub = ("אימונים בכרטיסייה" if lookup["package_kind"] == "pack"
                       else "מנוי חודשי")
            status_label = (config.PAYMENT_STATUSES.get(lookup["payment_status"])
                           if lookup["payment_status"] else "—")
            status_accent = ("good" if lookup["payment_status"] == "Paid"
                            else "warning" if lookup["payment_status"] == "Partial"
                            else "critical" if lookup["payment_status"] == "Pending"
                            else "blue")
            kpi_row([
                {"label": "כרטיסייה / מנוי", "value": pack_value,
                 "sub": pack_sub, "accent": "good"},
                {"label": "סטטוס תשלום", "value": status_label or "—",
                 "accent": status_accent},
                {"label": "אימונים קרובים שנרשמת אליהם",
                 "value": str(lookup["upcoming_count"]), "accent": "blue"},
            ])
        else:
            hello = f"היי {lookup['name']}, " if lookup else ""
            st.info(
                f"{hello}לא נמצאה כרטיסייה פעילה עם אימונים זמינים למספר הזה. "
                "שלחו לארז הודעה ונדאג לזה")
            whatsapp_button("היי ארז, אשמח לחדש כרטיסייה!",
                            logic.renewal_whatsapp_link())

    st.divider()

    today = date.today()
    lookahead_end = today + timedelta(days=config.SCHEDULE_LOOKAHEAD_DAYS - 1)
    all_slots = cached_schedule_slots(_ver(), today.isoformat(),
                                      lookahead_end.isoformat())

    if all_slots.empty:
        st.info("No upcoming sessions scheduled yet / אין עדיין אימונים מתוכננים.")
        return

    max_offset = max(0, (config.SCHEDULE_LOOKAHEAD_DAYS - 1) // 7)
    offset = _week_nav("public", max_offset)
    days = _week_days(offset)
    grouped = logic.slots_by_date(all_slots, days)

    with st.container(key="week_grid_public"):
        cols = st.columns(7)
        for col, d in zip(cols, days):
            with col:
                st.markdown(
                    f'<div class="gug-week-day-header">{logic.derive_day_of_week(d)}'
                    f'<br><span class="gug-week-day-date">{d.strftime("%d/%m")}'
                    f'</span></div>', unsafe_allow_html=True)
                day_slots = grouped[d.isoformat()]
                if day_slots.empty:
                    st.markdown('<div class="gug-week-empty">אין אימונים</div>',
                               unsafe_allow_html=True)
                    continue
                for _, row in day_slots.iterrows():
                    slot_id = int(row["id"])
                    available = int(row["available_seats"])
                    is_registered = bool(
                        lookup and slot_id in lookup["upcoming_booking_slot_ids"])
                    st.markdown(_slot_card_html(row, registered=is_registered),
                               unsafe_allow_html=True)

                    if is_registered:
                        booking_id = logic.find_client_booking_id(
                            slot_id, lookup["client_id"])
                        if booking_id and st.button(
                                "ביטול הרשמה", key=f"cancel_{slot_id}",
                                width="stretch"):
                            try:
                                logic.remove_slot_booking(booking_id)
                                bump_data()
                                st.success("ההרשמה בוטלה.")
                                st.rerun()
                            except logic.ValidationError as e:
                                st.error(str(e))
                        continue

                    if available <= 0:
                        continue
                    if lookup and can_book:
                        if st.button("אני רוצה להירשם", key=f"selfbook_{slot_id}",
                                     width="stretch"):
                            try:
                                result = logic.book_client_to_slot(
                                    slot_id, lookup["client_id"])
                                bump_data()
                                remaining = result["session_result"]["remaining"]
                                if remaining is None:
                                    st.success("נרשמת בהצלחה!")
                                else:
                                    st.success(f"נרשמת! נותרו {remaining} אימונים.")
                                st.rerun()
                            except logic.ValidationError as e:
                                st.error(str(e))
                    else:
                        link = logic.booking_whatsapp_link(
                            row["session_type"], row["day_of_week"],
                            row["date"], row["start_time"])
                        whatsapp_button("בקשת הרשמה", link)


def _slot_edit_popover(slot):
    slot_id = int(slot["id"])
    with st.form(f"edit_slot_{slot_id}"):
        e_coach_label = st.radio(
            "Coach", list(config.TRAINERS.values()), horizontal=True,
            index=list(config.TRAINERS.keys()).index(slot["coach_name"]))
        e_date = st.date_input("Date", value=date.fromisoformat(slot["date"]))
        ec1, ec2 = st.columns(2)
        with ec1:
            e_start = st.time_input("Start", value=datetime.strptime(
                slot["start_time"], "%H:%M").time())
        with ec2:
            e_end = st.time_input("End", value=datetime.strptime(
                slot["end_time"], "%H:%M").time())
        e_type_label = st.radio(
            "Session type", list(config.SESSION_TYPES.values()), horizontal=True,
            index=list(config.SESSION_TYPES.keys()).index(slot["session_type"]))
        e_capacity = st.number_input(
            "Max capacity", min_value=1, step=1, value=int(slot["max_capacity"]))
        e_notes = st.text_input("Notes", value=slot["notes"] or "")
        e_submitted = st.form_submit_button("Save changes")
    if e_submitted:
        try:
            e_coach = [k for k, v in config.TRAINERS.items()
                      if v == e_coach_label][0]
            e_session_type = [k for k, v in config.SESSION_TYPES.items()
                              if v == e_type_label][0]
            logic.update_schedule_slot(
                int(slot["id"]), e_coach, e_date, e_start.strftime("%H:%M"),
                e_end.strftime("%H:%M"), e_session_type, int(e_capacity), e_notes)
            bump_data()
            st.success("Slot updated.")
            st.rerun()
        except logic.ValidationError as e:
            st.error(str(e))
        except Exception as e:
            st.error(f"Could not update the slot: {e}")


def _slot_add_client_popover(slot):
    slot_id = int(slot["id"])
    client_id, _ = client_selector("Client", key=f"slot_client_{slot_id}")
    if client_id and st.button("Assign to slot", key=f"assign_{slot_id}"):
        try:
            result = logic.book_client_to_slot(slot_id, client_id)
            bump_data()
            sess = result["session_result"]
            msg = "Client assigned — 1 session deducted."
            if sess["deducted"]:
                msg = (f"Client assigned — 1 session deducted, "
                      f"**{sess['remaining']}** remaining.")
            elif sess["overdraft"]:
                msg = "Client assigned as an **Overdraft / חריגה** session."
            st.success(msg)
            st.rerun()
        except logic.ValidationError as e:
            st.error(str(e))
        except Exception as e:
            st.error(f"Could not assign the client: {e}")


def _slot_add_dropin_popover(slot):
    slot_id = int(slot["id"])
    with st.form(f"dropin_form_{slot_id}", clear_on_submit=True):
        name = st.text_input("Name / שם")
        c1, c2 = st.columns(2)
        with c1:
            price = st.number_input("Price (ILS)", min_value=0.0, step=10.0)
        with c2:
            pay_method = st.selectbox("Payment method", config.PAYMENT_METHODS,
                                      key=f"pm_{slot_id}")
        submitted = st.form_submit_button("Log Drop-in")
    if submitted:
        try:
            result = logic.book_dropin_to_slot(slot_id, name, price, pay_method)
            bump_data()
            st.success(f"Drop-in logged — {ils(price)} recorded and split "
                      "by the payout engine.")
            st.rerun()
        except logic.ValidationError as e:
            st.error(str(e))
        except Exception as e:
            st.error(f"Could not log the drop-in: {e}")


def _slot_roster_and_removal(bookings):
    if bookings.empty:
        st.caption("No one booked yet.")
        return
    for _, b in bookings.iterrows():
        bc1, bc2 = st.columns([4, 1])
        display_name = (b["client_name"] if b["booking_type"] == "client"
                        else f"{b['dropin_name']} (מזדמן)")
        bc1.write(display_name)
        if bc2.button("X", key=f"rm_booking_{b['id']}", help="Remove from this slot"):
            try:
                result = logic.remove_slot_booking(int(b["id"]))
                bump_data()
                if result["session_restored"] and result["session_restored"]["restored"]:
                    st.success("Removed — 1 session restored to their pack.")
                else:
                    st.success("Removed from the slot.")
                st.rerun()
            except logic.ValidationError as e:
                st.error(str(e))


def _slot_cancel_control(slot_id: int, attendees: int):
    """Two-step confirm (not a bare click) — deleting a slot does NOT
    reverse attendees' pack deductions (same as before this redesign), so a
    stray click on a now much more prominent quick-action icon is riskier
    than it used to be buried inside an expander."""
    confirm_key = f"confirm_del_{slot_id}"
    if st.session_state.get(confirm_key):
        if attendees > 0:
            st.caption("Won't restore attendees' sessions.")
        cc1, cc2 = st.columns(2)
        if cc1.button("YES", key=f"del_yes_{slot_id}", help="Confirm delete",
                      width="stretch"):
            logic.delete_schedule_slot(slot_id)
            bump_data()
            st.session_state.pop(confirm_key, None)
            st.success("Slot deleted.")
            st.rerun()
        if cc2.button("NO", key=f"del_no_{slot_id}", help="Cancel",
                      width="stretch"):
            st.session_state.pop(confirm_key, None)
            st.rerun()
    else:
        if st.button("DEL", key=f"del_ask_{slot_id}", help="Cancel slot",
                     width="stretch"):
            st.session_state[confirm_key] = True
            st.rerun()


def _admin_slot_card(slot):
    slot_id = int(slot["id"])
    available = int(slot["available_seats"])
    st.markdown(_slot_card_html(slot), unsafe_allow_html=True)

    bookings = cached_slot_bookings(_ver(), slot_id)
    if not bookings.empty:
        names = [
            (b["client_name"] if b["booking_type"] == "client"
             else f"{b['dropin_name']} (מזדמן)")
            for _, b in bookings.iterrows()
        ]
        st.markdown('<div class="gug-slot-roster">' +
                   "".join(f"• {n}<br>" for n in names) + '</div>',
                   unsafe_allow_html=True)
    if slot["notes"]:
        st.caption(f"{slot['notes']}")

    r1c1, r1c2 = st.columns(2)
    with r1c1:
        with st.popover("EDIT", help="Edit slot", width="stretch"):
            _slot_edit_popover(slot)
    with r1c2:
        _slot_cancel_control(slot_id, int(slot["current_attendees_count"]))

    r2c1, r2c2 = st.columns(2)
    with r2c1:
        with st.popover("ADD", help="Add active client", width="stretch",
                        disabled=available <= 0):
            _slot_add_client_popover(slot)
    with r2c2:
        with st.popover("DROP-IN", help="Add drop-in", width="stretch",
                        disabled=available <= 0):
            _slot_add_dropin_popover(slot)

    if not bookings.empty:
        with st.expander(f"נרשמים ({len(bookings)})"):
            _slot_roster_and_removal(bookings)


def render_admin_schedule_view():
    tab_add, tab_manage = st.tabs(["Add Slot", "Manage Slots"])

    with tab_add:
        with st.form("add_slot", clear_on_submit=True):
            coach_label = st.radio("Coach / מאמן", list(config.TRAINERS.values()),
                                   horizontal=True)
            slot_date = st.date_input("Date", value=date.today() + timedelta(days=1))
            c1, c2 = st.columns(2)
            with c1:
                start_t = st.time_input("Start time", value=datetime.strptime(
                    "18:00", "%H:%M").time())
            with c2:
                end_t = st.time_input("End time", value=datetime.strptime(
                    "19:00", "%H:%M").time())
            type_label = st.radio("Session type", list(config.SESSION_TYPES.values()),
                                  horizontal=True)
            capacity = st.number_input("Max capacity", min_value=1, step=1,
                                       value=config.DEFAULT_SLOT_CAPACITY)
            notes = st.text_input("Notes (optional)")
            submitted = st.form_submit_button("Add Slot")

        if submitted:
            try:
                coach = [k for k, v in config.TRAINERS.items()
                        if v == coach_label][0]
                session_type = [k for k, v in config.SESSION_TYPES.items()
                                if v == type_label][0]
                logic.create_schedule_slot(
                    coach, slot_date, start_t.strftime("%H:%M"),
                    end_t.strftime("%H:%M"), session_type, int(capacity), notes)
                bump_data()
                st.success(f"Slot added: {type_label} on {slot_date} "
                          f"{start_t.strftime('%H:%M')}–{end_t.strftime('%H:%M')}.")
            except logic.ValidationError as e:
                st.error(str(e))
            except Exception as e:
                st.error(f"Could not add the slot: {e}")

    with tab_manage:
        include_past = st.checkbox("Include past slots")
        from_date = None if include_past else date.today().isoformat()
        all_slots = cached_schedule_slots(_ver(), from_date, None)

        if all_slots.empty:
            st.info("No schedule slots yet — add one in the Add Slot tab.")
            return

        slot_dates = pd.to_datetime(all_slots["date"]).dt.date
        earliest = min(slot_dates.min(), date.today())
        latest = max(slot_dates.max(), date.today())
        min_offset = min(0, (earliest - date.today()).days // 7)
        max_offset = max(0, (latest - date.today()).days // 7)

        offset = _week_nav("admin", max_offset, min_offset)
        days = _week_days(offset)
        grouped = logic.slots_by_date(all_slots, days)

        with st.container(key="week_grid_admin"):
            cols = st.columns(7)
            for col, d in zip(cols, days):
                with col:
                    st.markdown(
                        f'<div class="gug-week-day-header">{logic.derive_day_of_week(d)}'
                        f'<br><span class="gug-week-day-date">{d.strftime("%d/%m")}'
                        f'</span></div>', unsafe_allow_html=True)
                    day_slots = grouped[d.isoformat()]
                    if day_slots.empty:
                        st.markdown('<div class="gug-week-empty">אין אימונים</div>',
                                   unsafe_allow_html=True)
                        continue
                    for _, row in day_slots.iterrows():
                        _admin_slot_card(row)


def page_schedule():
    st.title("Schedule — מערכת שעות")
    public_view = st.toggle(
        "Public Client View / תצוגת לקוח",
        help="Preview exactly what a client sees — a visual weekly "
             "timetable with WhatsApp/self-service booking.")
    if public_view:
        render_public_schedule_view()
    else:
        render_admin_schedule_view()


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Page 5 — Settings / גיבוי והגדרות
# ---------------------------------------------------------------------------
def page_settings():
    st.title("Settings — גיבוי והגדרות")

    st.subheader("Database backup")
    st.caption("A consistent snapshot of the entire database "
               "(taken via SQLite's online-backup API — safe to run anytime).")
    st.download_button(
        "Download database backup (gug_crm.db)",
        data=logic.backup_db_bytes(),
        file_name=f"gug_crm_backup_{date.today().isoformat()}.db",
        mime="application/octet-stream",
    )

    st.subheader("Export to Excel")
    st.caption("All core tables — Clients, Packages, Sessions History, "
               "Finance, Open Debts — in one .xlsx workbook.")
    st.download_button(
        "Download Excel export (all tables)",
        data=logic.export_excel_bytes(),
        file_name=f"gug_export_{date.today().isoformat()}.xlsx",
        mime=("application/vnd.openxmlformats-officedocument"
              ".spreadsheetml.sheet"),
    )

    st.subheader("Database info")
    counts = logic.table_counts()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Clients", counts["clients"])
    c2.metric("Packages", counts["packages"])
    c3.metric("Sessions", counts["sessions"])
    c4.metric("Transactions", counts["financial_transactions"])
    st.caption("Tip: download a backup before large manual edits — restoring "
               "is as simple as replacing gug_crm.db with the backup file.")

    st.divider()
    st.subheader("Reset Database / איפוס נתונים")
    st.caption(
        "Permanently deletes every client, package, session and financial "
        "transaction, and resets ID counters back to 1. The schema, tables "
        "and relationships stay fully intact — the app is immediately ready "
        "for real data afterward. **This cannot be undone.** Download a "
        "backup above first if you might need this data again.")

    with st.expander("Open the danger zone"):
        reset_msg = st.session_state.pop("reset_db_msg", None)
        if reset_msg:
            st.success(reset_msg)

        total_rows = sum(counts.values())
        if total_rows == 0:
            st.info("The database is already empty — nothing to reset.")
        else:
            st.warning(
                f"This will permanently delete **{total_rows}** record(s): "
                f"{counts['clients']} clients, {counts['packages']} packages, "
                f"{counts['sessions']} sessions, "
                f"{counts['financial_transactions']} transactions, "
                f"{counts['payout_adjustments']} payout adjustments, "
                f"{counts['weekly_schedule']} schedule slots and "
                f"{counts['schedule_bookings']} slot bookings.")

            confirmed = st.checkbox(
                "I understand this will permanently delete all data and "
                "cannot be undone.",
                key="reset_confirm_checkbox")
            typed = st.text_input(
                f"Type '{logic.RESET_CONFIRMATION_PHRASE}' to confirm",
                key="reset_confirm_text")
            ready = (confirmed and
                    typed.strip() == logic.RESET_CONFIRMATION_PHRASE)

            if st.button("Reset Database", type="primary",
                        disabled=not ready):
                try:
                    deleted = logic.reset_database(typed)
                    bump_data()
                    st.session_state["reset_db_msg"] = (
                        f"Database reset complete — "
                        f"{sum(deleted.values())} record(s) deleted across "
                        f"{len(deleted)} tables. IDs restart at 1 — ready "
                        "for real data.")
                    st.rerun()
                except logic.ValidationError as e:
                    st.error(str(e))
                except Exception as e:
                    st.error(f"Could not reset the database: {e}")


# ---------------------------------------------------------------------------
# Router (with a global error boundary so a failure in one page never
# crashes the whole app — data is safe, the user sees a friendly notice).
#
# Access control lives HERE, not just in the sidebar: an unauthenticated
# session can only ever reach render_public_schedule_view() — no PAGES
# lookup, no admin page function, is even called unless is_admin is True.
# Hiding the sidebar radio is a UX nicety; this branch is the real boundary.
# ---------------------------------------------------------------------------
PAGES = {
    "Clients & Packages": page_clients,
    "Log & Calendar / תיעוד ויומן אימונים": page_log_calendar,
    "Schedule / מערכת שעות": page_schedule,
    "Finance (Income & Expenses)": page_finance,
    "Overview Dashboard": page_dashboard,
    "Settings / גיבוי והגדרות": page_settings,
}

try:
    if st.session_state["is_admin"]:
        PAGES[page]()
    else:
        st.title("GUG CRM")
        render_public_schedule_view()
except logic.ValidationError as e:
    st.error(str(e))
except Exception as e:  # pragma: no cover — last-resort boundary
    st.error("Something went wrong on this page — your data is safe. "
             "Details below; try again or switch pages.")
    st.exception(e)
