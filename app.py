"""
Jetking Feedback Dashboards — Streamlit app
Live-connects to the Technical and Employability Session Feedback Google Sheets
via each sheet's "Publish to web" CSV link (no Google Cloud project, service
account, or Apps Script needed) and renders the same breakdowns as the HTML
preview dashboards: KPIs, trend over time, by Centre / Mentor / Course & Batch
(split into the two question categories), and a weakest-to-strongest question
ranking per category. Centre and Mentor filters combine.

Setup: see SETUP_GUIDE.md in this folder.
"""

import html
import io
import time
from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

import streamlit as st
import pandas as pd
import requests
import plotly.graph_objects as go

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

st.set_page_config(page_title="Jetking Feedback Dashboards", layout="wide", page_icon="🎓")

CACHE_TTL_SECONDS = 300  # 5 minutes

PALETTE = {
    "series1": "#2a78d6",   # blue — Session Delivery / Session Content
    "series2": "#eb6834",   # orange — Mentor Behavior
    "series3": "#1baf7a",   # aqua — neutral accent (e.g. response counts)
    "serious": "#d03b3b",   # flag color for scores below 3
    "muted": "#898781",
    "grid": "#e1e0d9",
}

# Page chrome: hero banner, elevated KPI cards color-coded per metric, section
# headers with an icon + accent rule, and lightly polished tabs/tables/buttons.
# Purely cosmetic — no effect on data or layout logic below.
st.markdown(f"""
<style>
.hero-banner {{
    background: linear-gradient(135deg, {PALETTE["series1"]} 0%, #184f95 100%);
    padding: 1.75rem 2rem;
    border-radius: 14px;
    margin-bottom: 1.5rem;
    box-shadow: 0 4px 18px rgba(24,79,149,0.28);
}}
.hero-banner h1 {{
    color: #ffffff;
    margin: 0;
    font-size: 2.1rem;
    font-weight: 700;
    line-height: 1.2;
}}
.hero-banner p {{
    color: rgba(255,255,255,0.88);
    margin: 0.4rem 0 0 0;
    font-size: 0.95rem;
}}

/* KPI scorecards — rendered as custom HTML (not st.metric) so each card's
   color can reflect its own value (Blue/Yellow/Red thresholds), computed
   in Python and passed in as inline style rather than a fixed CSS rule. */
.kpi-card {{
    background: #ffffff;
    border-radius: 12px;
    padding: 1.1rem 0.5rem 1rem;
    box-shadow: 0 1px 3px rgba(11,11,11,0.08), 0 1px 2px rgba(11,11,11,0.05);
    border: 1px solid {PALETTE["grid"]};
    border-top: 4px solid {PALETTE["series1"]};
    text-align: center;
    transition: box-shadow 0.15s ease;
}}
.kpi-card:hover {{
    box-shadow: 0 4px 14px rgba(11,11,11,0.12);
}}
.kpi-label {{
    font-size: 0.875rem;
    color: {PALETTE["muted"]};
    margin-bottom: 0.35rem;
}}
.kpi-value {{
    font-size: 1.9rem;
    font-weight: 700;
}}

/* Comments panel: a plain HTML table (not st.dataframe) so long free-text
   answers — Suggestions especially — wrap instead of truncating. */
.wrapped-table-container {{
    overflow-x: auto;
    border-radius: 10px;
    box-shadow: 0 1px 3px rgba(11,11,11,0.06);
    border: 1px solid {PALETTE["grid"]};
    margin-bottom: 0.5rem;
}}
.wrapped-table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 0.9rem;
}}
.wrapped-table th {{
    background: {PALETTE["grid"]}55;
    text-align: left;
    padding: 0.55rem 0.75rem;
    font-weight: 700;
    border-bottom: 2px solid {PALETTE["grid"]};
    white-space: nowrap;
}}
.wrapped-table td {{
    padding: 0.55rem 0.75rem;
    border-bottom: 1px solid {PALETTE["grid"]};
    white-space: normal;
    overflow-wrap: break-word;
    min-width: 120px;  /* a short value (a name, a city) never gets squeezed into a mid-word break */
    vertical-align: top;
}}
.wrapped-table tbody tr:nth-child(even) {{ background: #fafaf8; }}

.section-header {{
    display: flex;
    align-items: center;
    gap: 0.55rem;
    margin: 1.9rem 0 0.85rem 0;
    padding-bottom: 0.45rem;
    border-bottom: 2px solid {PALETTE["grid"]};
}}
.section-header .icon {{ font-size: 1.35rem; line-height: 1; }}
.section-header h3 {{ margin: 0; font-size: 1.28rem; font-weight: 700; }}

button[data-baseweb="tab"] {{
    font-size: 1.05rem;
    font-weight: 600;
}}

div[data-testid="stDataFrame"] {{
    border-radius: 10px;
    overflow: hidden;
    box-shadow: 0 1px 3px rgba(11,11,11,0.06);
}}

div[data-testid="stButton"] button, div[data-testid="stFormSubmitButton"] button {{
    border-radius: 8px;
    font-weight: 600;
}}

div[data-testid="stVerticalBlockBorderWrapper"] {{
    border-radius: 12px;
    box-shadow: 0 1px 4px rgba(11,11,11,0.06);
}}
</style>
""", unsafe_allow_html=True)


def section_header(icon, text):
    """A styled subheader with an icon and an accent underline — used in place
    of st.subheader() throughout the dashboard for visual consistency."""
    st.markdown(
        f'<div class="section-header"><span class="icon">{icon}</span><h3>{text}</h3></div>',
        unsafe_allow_html=True,
    )

# Diverging blue<->red scale for the heatmap (magnitude with a meaningful
# neutral midpoint at 3/5) and the 5-point Likert distribution chart —
# darker = a stronger opinion on that side, gray = neutral.
LIKERT_LABELS = {1: "Strongly Disagree", 2: "Disagree", 3: "Neutral", 4: "Agree", 5: "Strongly Agree"}
LIKERT_COLORS = {1: "#d03b3b", 2: "#ec835a", 3: "#c3c2b7", 4: "#6da7ec", 5: "#184f95"}
HEATMAP_COLORSCALE = [[0.0, "#d03b3b"], [0.5, "#f0efec"], [1.0, "#2a78d6"]]

# Question maps: (code, exact form question text, short display label, category)
# The exact text is what's used to match the sheet's column headers; the short
# label is what actually renders in charts/lists — full text is still one hover
# away on the heatmap. category "cat1" = delivery/content questions, "cat2" =
# mentor behavior questions.
TECH_Q = [
    ("D1", "Are the practical sessions in Arjuna Lab conducted according to the logsheet?", "Lab sessions per logsheet", "cat1"),
    ("D2", "Have you received your Kingdemy ID within 30 days of the batch starting date?", "Kingdemy ID within 30 days", "cat1"),
    ("D3", "Are you able to access the content on Learnyst LMS? (Kingdemy)", "LMS content access", "cat1"),
    ("D4", "During Drona class, is your mentor conducting the SLP activities like student presentation / mindmap / group discussion / yoga / think-pair-share ?", "SLP activities in class", "cat1"),
    ("D5", "Does your mentor provide Presee's/Pre-reads materials for students to prepare before coming to class?", "Pre-read materials provided", "cat1"),
    ("D6", "Does your mentor display the content on the TV monitor in the class room uploaded to the Learnyst LMS? (Kingdemy)", "Content shown on TV monitor", "cat1"),
    ("D7", "Does your mentor consistently start classes on time?", "Classes start on time", "cat1"),
    ("D8", "Are you able to understand the concepts and skills taught by your mentor, and do you feel confident in applying them?", "Understands & applies concepts", "cat1"),
    ("B1", "Your mentor treats all students with respect and professionalism.", "Respect & professionalism", "cat2"),
    ("B2", "Your mentor encourages student participation and interaction during the session.", "Encourages participation", "cat2"),
    ("B3", "Your mentor is approachable and willing to clarify doubts.", "Approachable, clarifies doubts", "cat2"),
    ("B4", "Your mentor creates a positive and motivating learning environment.", "Positive, motivating environment", "cat2"),
    ("B5", "Your mentor provides constructive feedback on assignments, presentations, and practical work.", "Constructive feedback on work", "cat2"),
    ("B6", "Your mentor demonstrates patience while explaining difficult concepts.", "Patient explaining concepts", "cat2"),
    ("B7", "Your mentor maintains discipline while ensuring a friendly classroom atmosphere.", "Discipline & friendly atmosphere", "cat2"),
    ("B8", "Your mentor encourages teamwork, collaboration, and peer learning.", "Encourages teamwork", "cat2"),
    ("B9", "Overall, you are satisfied with your mentor's teaching and mentoring approach.", "Overall mentor satisfaction", "cat2"),
]

EMP_Q = [
    ("S1", "Does your mentor consistently start classes on time?", "Classes start on time", "cat1"),
    ("S2", "Are the Employability classes conducted according to the logsheet?", "Classes per logsheet", "cat1"),
    ("S3", "Are you able to understand the topics taught by your mentor and are you able to apply them confidently?", "Understands & applies topics", "cat1"),
    ("S4", "The session was engaging and interactive.", "Engaging & interactive", "cat1"),
    ("S5", "The activities helped you participate actively", "Activities encouraged participation", "cat1"),
    ("S6", "You felt comfortable expressing your thoughts and opinions.", "Comfortable expressing opinions", "cat1"),
    ("S7", "The session improved your confidence level.", "Improved confidence", "cat1"),
    ("S8", "The session was relevant to interview preparation.", "Relevant to interview prep", "cat1"),
    ("S9", "Overall, you are satisfied with today's session.", "Overall session satisfaction", "cat1"),
    ("B1", "Your mentor treats all students with respect and professionalism.", "Respect & professionalism", "cat2"),
    ("B2", "Your mentor encourages student participation and interaction during the session.", "Encourages participation", "cat2"),
    ("B3", "Your mentor is approachable and willing to clarify doubts.", "Approachable, clarifies doubts", "cat2"),
    ("B4", "Your mentor creates a positive and motivating learning environment.", "Positive, motivating environment", "cat2"),
    ("B5", "Your mentor provides constructive feedback on assignments, presentations, and practical work.", "Constructive feedback on work", "cat2"),
    ("B6", "Your mentor demonstrates patience while explaining difficult concepts.", "Patient explaining concepts", "cat2"),
    ("B7", "Your mentor maintains discipline while ensuring a friendly classroom atmosphere.", "Discipline & friendly atmosphere", "cat2"),
    ("B8", "Your mentor encourages teamwork, collaboration, and peer learning.", "Encourages teamwork", "cat2"),
    ("B9", "Overall, you are satisfied with your mentor's teaching and mentoring approach.", "Overall mentor satisfaction", "cat2"),
]

# Free-text columns to surface in the "Student comments" panel:
# (exact form question text, short display label)
TECH_COMMENT_COLS = [
    ("What did you learn in today's session?", "What they learned"),
    ("Please specify the topics that you have not understood", "What wasn't understood"),
    ("Please provide suggestions for improvements (if any):", "Suggestions"),
]

EMP_COMMENT_COLS = [
    ("What did you do and learn in today's session?", "What they learned"),
    ("Please specify the topics that you have not understood", "What wasn't understood"),
    ("What action will you take before the next session?", "Next action"),
    ("Share one word that describes today's session. (Eg. Fun, Exhilerating, Inspiring, Motivating.. etc.)", "One-word summary"),
    ("Please provide suggestions for improvements (if any):", "Suggestions"),
]

# Categorical (non-1-5) questions: Yes/No/Not Sure and worded confidence
# scales don't share a scale with the Likert questions above, so they're
# never averaged into the KPI/score charts — instead each gets its own
# % breakdown bar in a dedicated "Additional questions" section. Options are
# listed worst-to-best (or Yes/No/Not Sure) so the stacked bar reads left to
# right; colors follow the same red=concern / gray=neutral / blue=positive
# language as the Likert distribution charts elsewhere, so the two visually
# match even though the scales differ. Empty for Technical (no such
# questions there yet).
TECH_SPECIAL_Q = []

EMP_SPECIAL_Q = [
    {
        "code": "PD1",
        "column": "Does the center have a dedicated Employability mentor (PD trainer)?",
        "short_label": "Centre has a dedicated PD trainer",
        "options": ["No", "Not Sure", "Yes"],
        "colors": {"No": PALETTE["serious"], "Not Sure": PALETTE["muted"], "Yes": "#0ca30c"},
    },
    {
        "code": "CQ1",
        "column": "How confident do you feel about facing an interview after today's session?",
        "short_label": "Interview confidence",
        "options": ["Not Confident", "Moderately Confident", "Very Confident"],
        "colors": {"Not Confident": PALETTE["serious"], "Moderately Confident": "#c3c2b7", "Very Confident": PALETTE["series1"]},
        # The live form's actual answer choice is misspelled ("Moderateluy
        # Confident") — map it to the correct label rather than fixing the
        # form (which would leave every response submitted before the fix
        # spelled the old way and silently excluded). Add more entries here
        # if another typo turns up as more responses come in.
        "value_aliases": {"Moderateluy Confident": "Moderately Confident"},
    },
    {
        "code": "CQ2",
        "column": "Compared to yesterday, your confidence level is:",
        "short_label": "Confidence vs. yesterday",
        "options": ["Lower", "Same as earlier", "Slightly Better", "Much Better"],
        "colors": {"Lower": PALETTE["serious"], "Same as earlier": "#c3c2b7", "Slightly Better": "#6da7ec", "Much Better": "#184f95"},
    },
]


# ---------------------------------------------------------------------------
# Data loading (live, cached)
# ---------------------------------------------------------------------------
#
# Each Sheet is "Published to web" as CSV (File -> Share -> Publish to web,
# see SETUP_GUIDE.md). That URL is fetched directly with no auth — this is
# the path that actually works on a locked-down Google Workspace domain,
# since the Apps Script Web App route hit an org policy requiring a signed-in
# jetking.com session even with "Anyone" access selected at deploy time.
# Note: a published sheet is viewable by anyone who has the URL, even
# without a Google login — the URL itself isn't guessable, but it isn't
# access-controlled either.

@st.cache_data(ttl=CACHE_TTL_SECONDS)
def load_sheet(endpoint_key):
    """Returns (df, fetched_at). fetched_at is stamped inside this cached
    function, so it reflects when the data was actually pulled — it only
    changes when the 5-minute cache expires or "Refresh data" is clicked.

    Google's publish-to-web endpoint occasionally responds slowly (more so
    over a corporate network/proxy) — retry a couple of times with backoff
    before giving up, rather than surfacing a one-off timeout as an error."""
    url = st.secrets["sheet_endpoints"][endpoint_key]
    last_err = None
    for attempt in range(3):
        try:
            resp = requests.get(url, timeout=45)
            resp.raise_for_status()
            df = pd.read_csv(io.StringIO(resp.text))
            df.columns = [str(c).replace("\n", " ").strip() for c in df.columns]
            return df, datetime.now(IST)
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            last_err = e
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
    raise last_err


def build_response_and_question_df(raw_df, centre_col, mentor_col, course_col, batch_col, qmap,
                                    ts_col="Timestamp", comment_cols=None, special_cols=None):
    """Returns (response_df, question_long_df).
    response_df: one row per response with centre/mentor/course/batch/date/cat1/cat2/avg/comments/special.
    question_long_df: one row per (response, question) with cat/code/label/score, plus dims for filtering.
    comment_cols: optional list of (raw column name, short display label) for free-text fields to
    carry through into response_df["comments"] (a dict per response) for the comments panel.
    special_cols: optional list of SPECIAL_Q-style dicts (Yes/No/Not Sure, worded confidence
    scales, ...) whose raw answers carry through into response_df["special"] (a dict per
    response, keyed by the spec's "code") for the categorical % breakdown charts. These are
    never averaged into cat1/cat2/avg — they're on a different scale entirely.
    """
    comment_cols = comment_cols or []
    special_cols = special_cols or []
    resp_rows = []
    q_rows = []
    for i, r in raw_df.iterrows():
        cat1_vals, cat2_vals = [], []
        row_q = []
        for code, question_text, short_label, cat in qmap:
            val = pd.to_numeric(r.get(question_text), errors="coerce")
            if pd.isna(val):
                continue
            row_q.append((code, short_label, cat, float(val)))
            (cat1_vals if cat == "cat1" else cat2_vals).append(float(val))
        if not row_q:
            continue
        ts = pd.to_datetime(r.get(ts_col), errors="coerce")
        centre = str(r.get(centre_col, "")).strip()
        mentor = str(r.get(mentor_col, "")).strip()
        course = str(r.get(course_col, "")).strip()
        batch = str(r.get(batch_col, "")).strip()
        cat1_avg = sum(cat1_vals) / len(cat1_vals) if cat1_vals else None
        cat2_avg = sum(cat2_vals) / len(cat2_vals) if cat2_vals else None
        all_vals = cat1_vals + cat2_vals
        # Always include all configured comment fields, even when the student's
        # actual answer was "None"/"N/A" — that's real signal (nothing to flag),
        # not a blank to hide. Only a genuinely empty cell falls back to "—".
        comments = {}
        for raw_col, disp_label in comment_cols:
            val = r.get(raw_col)
            text = "" if pd.isna(val) else str(val).strip()
            comments[disp_label] = text if text else "—"
        # Special (non-1-5) questions: keep the raw answer text as-is, or None
        # if blank — unrecognized/blank values are simply excluded when the
        # % breakdown is computed later, rather than shown as a fallback dash.
        special = {}
        for spec in special_cols:
            val = r.get(spec["column"])
            text = "" if pd.isna(val) else str(val).strip()
            special[spec["code"]] = text if text else None
        resp_rows.append({
            "date": ts.date().isoformat() if pd.notna(ts) else None,
            "centre": centre, "mentor": mentor, "course": course, "batch": batch,
            "cat1": round(cat1_avg, 2) if cat1_avg is not None else None,
            "cat2": round(cat2_avg, 2) if cat2_avg is not None else None,
            "avg": round(sum(all_vals) / len(all_vals), 2) if all_vals else None,
            "comments": comments,
            "special": special,
        })
        for code, label, cat, score in row_q:
            q_rows.append({
                "centre": centre, "mentor": mentor,
                "code": code, "label": label, "cat": cat, "score": score,
            })
    return pd.DataFrame(resp_rows), pd.DataFrame(q_rows)


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def group_avg(df, key_col):
    if df.empty:
        return pd.DataFrame(columns=["label", "cat1", "cat2", "avg", "n"])
    g = df.groupby(key_col).agg(
        cat1=("cat1", "mean"), cat2=("cat2", "mean"), avg=("avg", "mean"), n=("avg", "count")
    ).reset_index()
    g.columns = ["label", "cat1", "cat2", "avg", "n"]
    g[["cat1", "cat2", "avg"]] = g[["cat1", "cat2", "avg"]].round(2)
    return g.sort_values("avg", ascending=False)


def trend_by_date(df):
    if df.empty:
        return pd.DataFrame(columns=["date", "avg"])
    g = df.dropna(subset=["date"]).groupby("date")["avg"].mean().round(2).reset_index()
    return g.sort_values("date")


def question_ranking(qdf, cat):
    sub = qdf[qdf["cat"] == cat]
    if sub.empty:
        return pd.DataFrame(columns=["label", "avg", "n"])
    g = sub.groupby("label")["score"].agg(avg="mean", n="count").reset_index()
    g["avg"] = g["avg"].round(2)
    return g.sort_values("avg", ascending=True)


def mentor_question_matrix(qdf, cat, qmap):
    """Pivot to mentor (rows) x question code (columns) average score, for the heatmap.
    Returns (matrix_df indexed by mentor, {code: short label} for hover)."""
    sub = qdf[qdf["cat"] == cat]
    if sub.empty:
        return pd.DataFrame(), {}
    code_text = {code: short_label for code, question_text, short_label, c in qmap if c == cat}
    codes = [c for c, _, _, cc in qmap if cc == cat]
    pivot = sub.pivot_table(index="mentor", columns="code", values="score", aggfunc="mean")
    pivot = pivot.reindex(columns=[c for c in codes if c in pivot.columns])
    return pivot, code_text


def question_distribution(qdf, cat):
    """Per-question % breakdown across the 5 Likert buckets, for the diverging stacked bar."""
    sub = qdf[qdf["cat"] == cat]
    if sub.empty:
        return pd.DataFrame()
    sub = sub.copy()
    sub["score"] = sub["score"].round().clip(1, 5).astype(int)
    counts = sub.groupby(["label", "score"]).size().unstack(fill_value=0)
    for v in (1, 2, 3, 4, 5):
        if v not in counts.columns:
            counts[v] = 0
    counts = counts[[1, 2, 3, 4, 5]]
    n = counts.sum(axis=1)
    pct = counts.div(n, axis=0) * 100
    pct["n"] = n
    pct["weighted_avg"] = sum(v * pct[v] for v in (1, 2, 3, 4, 5)) / 100
    return pct.reset_index().sort_values("weighted_avg", ascending=True)


def build_action_items(filt_resp, filt_q, cat1_label, cat2_label, threshold=3.0):
    """Consolidated list of every mentor/centre/course/question currently below
    threshold, worst first, each tagged with its response count and which of
    the two question categories it belongs to."""
    items = []
    for scope, key_col in [("Learning Centre", "centre"), ("Mentor/Faculty", "mentor"), ("Course", "course")]:
        g = group_avg(filt_resp, key_col)
        for _, row in g.iterrows():
            for cat_col, cat_lbl in [("cat1", cat1_label), ("cat2", cat2_label)]:
                if pd.notna(row[cat_col]) and row[cat_col] < threshold:
                    items.append({
                        "Category": cat_lbl, "Area": scope, "Item": row["label"],
                        "Avg Score": row[cat_col], "Responses": int(row["n"]),
                    })
    for cat_col, cat_lbl in [("cat1", cat1_label), ("cat2", cat2_label)]:
        qr = question_ranking(filt_q, cat_col)
        for _, row in qr[qr["avg"] < threshold].iterrows():
            items.append({
                "Category": cat_lbl, "Area": "Question", "Item": row["label"],
                "Avg Score": row["avg"], "Responses": int(row["n"]),
            })
    items.sort(key=lambda d: d["Avg Score"])
    return items


# ---------------------------------------------------------------------------
# Chart builders
# ---------------------------------------------------------------------------

def score_color(score):
    """Blue/Yellow/Red thresholds for KPI scorecards: >3.5 is healthy (blue),
    2.5-3.49 is borderline (yellow), <2.5 needs attention (red). A score of
    None (no responses yet, or not a 1-5 score at all) stays neutral gray."""
    if score is None:
        return {"bg": "#ffffff", "border": PALETTE["muted"], "text": "#0b0b0b"}
    if score > 3.5:
        return {"bg": "#eaf2fc", "border": PALETTE["series1"], "text": "#184f95"}
    elif score >= 2.5:
        return {"bg": "#fff6e0", "border": "#c98500", "text": "#7a5200"}
    else:
        return {"bg": "#fceceb", "border": PALETTE["serious"], "text": "#8a1f1f"}


def kpi_card(label, display_value, score_for_color, help_text=None):
    """Renders one KPI scorecard as custom HTML — needed (rather than
    st.metric) so its color can be computed from its own value at render
    time instead of a fixed position-based CSS rule."""
    c = score_color(score_for_color)
    title_attr = f' title="{html.escape(help_text)}"' if help_text else ""
    st.markdown(
        f'<div class="kpi-card" style="background:{c["bg"]}; border-top-color:{c["border"]};"{title_attr}>'
        f'<div class="kpi-label">{html.escape(label)}</div>'
        f'<div class="kpi-value" style="color:{c["text"]};">{html.escape(str(display_value))}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )


def kpi_row(overall, cat1, cat2, count, cat1_label, cat2_label):
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        kpi_card(
            "Overall average score", f"{overall:.2f}" if overall is not None else "—", overall,
            help_text="Out of 5" if overall is not None else "No responses yet",
        )
    with c2:
        kpi_card(cat1_label, f"{cat1:.2f}" if cat1 is not None else "—", cat1)
    with c3:
        kpi_card(cat2_label, f"{cat2:.2f}" if cat2 is not None else "—", cat2)
    with c4:
        kpi_card("Responses in selection", count, None)  # a count, not a 1-5 score — stays neutral


def trend_chart(trend_df):
    if trend_df.empty:
        st.info("No responses yet — a day-by-day average will appear here once data comes in.")
        return
    dates = pd.to_datetime(trend_df["date"])
    fig = go.Figure()
    fig.add_scatter(
        x=dates, y=trend_df["avg"], mode="lines+markers",
        line=dict(color=PALETTE["series1"], width=2),
        marker=dict(size=7, color=PALETTE["series1"]),
        fill="tozeroy", fillcolor="rgba(42,120,214,0.10)",
        hovertemplate="%{x|%b %d, %Y}<br><b>%{y:.2f}</b> / 5<extra></extra>",
        name="Daily average",
    )
    # With a single data point, Plotly's autorange falls back to sub-second
    # tick spacing. Force an explicit date axis and pad the range so one
    # point still reads as a clean day on the x-axis.
    span_days = (dates.max() - dates.min()).days
    if len(dates) == 1 or span_days < 1:
        x_range = [dates.min() - pd.Timedelta(days=1), dates.max() + pd.Timedelta(days=1)]
        dtick = 24 * 60 * 60 * 1000  # one tick per day, in ms (Plotly date-axis convention)
    else:
        pad = pd.Timedelta(days=max(1, round(span_days * 0.1)))
        x_range = [dates.min() - pad, dates.max() + pad]
        dtick = None  # let Plotly choose a sensible interval for a wider spread
    xaxis_kwargs = dict(type="date", tickformat="%b %d, %Y", range=x_range, gridcolor=PALETTE["grid"])
    if dtick:
        xaxis_kwargs["dtick"] = dtick
    fig.update_layout(
        yaxis=dict(range=[0, 5], gridcolor=PALETTE["grid"]),
        xaxis=xaxis_kwargs,
        height=280, margin=dict(t=10, b=10, l=10, r=10),
        plot_bgcolor="white", showlegend=False,
    )
    st.plotly_chart(fig, width="stretch")


def grouped_breakdown_chart(df_group, cat1_label, cat2_label):
    if df_group.empty:
        st.info("No responses yet — this will populate once responses come in.")
        return
    df_group = df_group.sort_values("avg", ascending=True)  # so highest ends up on top (horizontal bar)
    fig = go.Figure()
    fig.add_bar(name=cat1_label, y=df_group["label"], x=df_group["cat1"], orientation="h",
                marker_color=PALETTE["series1"],
                hovertemplate="%{y}<br>" + cat1_label + ": <b>%{x:.2f}</b> / 5<extra></extra>")
    fig.add_bar(name=cat2_label, y=df_group["label"], x=df_group["cat2"], orientation="h",
                marker_color=PALETTE["series2"],
                hovertemplate="%{y}<br>" + cat2_label + ": <b>%{x:.2f}</b> / 5<extra></extra>")
    fig.update_layout(
        barmode="group", xaxis=dict(range=[0, 5], gridcolor=PALETTE["grid"]),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        height=110 + 55 * len(df_group), margin=dict(t=40, b=10, l=10, r=10),
        plot_bgcolor="white",
    )
    st.plotly_chart(fig, width="stretch")
    flagged = df_group[(df_group["cat1"] < 3) | (df_group["cat2"] < 3)]
    if not flagged.empty:
        st.caption("⚠️ Needs attention (below 3/5): " + ", ".join(flagged["label"].tolist()))


def question_chart(qrank_df, color, label):
    if qrank_df.empty:
        st.info("No responses yet.")
        return
    fig = go.Figure()
    fig.add_bar(
        y=qrank_df["label"], x=qrank_df["avg"], orientation="h",
        marker_color=color, text=qrank_df["avg"].map(lambda v: f"{v:.2f}"),
        textposition="outside",
        cliponaxis=False,  # otherwise labels near the right edge get cut off
        hovertemplate="%{y}<br><b>%{x:.2f}</b> / 5<extra></extra>",
    )
    fig.update_layout(
        xaxis=dict(range=[0, 6], gridcolor=PALETTE["grid"]),
        height=80 + 40 * len(qrank_df), margin=dict(t=10, b=10, l=10, r=40),
        plot_bgcolor="white", showlegend=False,
        yaxis=dict(autorange="reversed"),
    )
    st.plotly_chart(fig, width="stretch")
    flagged = qrank_df[qrank_df["avg"] < 3]
    if not flagged.empty:
        st.caption(f"⚠️ Needs attention (below 3/5), {label}: " + ", ".join(flagged["label"].tolist()))


def heatmap_chart(matrix_df, code_text):
    if matrix_df.empty:
        st.info("No responses yet.")
        return
    z = matrix_df.values
    fig = go.Figure(go.Heatmap(
        z=z, x=matrix_df.columns.tolist(), y=matrix_df.index.tolist(),
        zmin=1, zmax=5, zmid=3, colorscale=HEATMAP_COLORSCALE,
        text=[[f"{v:.1f}" if pd.notna(v) else "" for v in row] for row in z],
        texttemplate="%{text}",
        customdata=[[code_text.get(c, c) for c in matrix_df.columns] for _ in matrix_df.index],
        hovertemplate="%{customdata}<br><b>%{z:.2f}</b> / 5<extra></extra>",
        colorbar=dict(title="Avg", tickvals=[1, 2, 3, 4, 5], lenmode="pixels", len=140, thickness=14),
        xgap=2, ygap=2,
    ))
    # Floor the height so a short heatmap (few mentors) still leaves the
    # colorbar's 5 tick labels enough room not to overlap.
    fig.update_layout(
        height=max(220, 80 + 36 * len(matrix_df.index)), margin=dict(t=10, b=10, l=10, r=10),
        plot_bgcolor="white",
        xaxis=dict(side="top", title="Question code (hover for question)"),
    )
    st.plotly_chart(fig, width="stretch")


def distribution_chart(dist_df):
    if dist_df.empty:
        st.info("No responses yet.")
        return
    labels = [f"{lbl}  (n={n})" for lbl, n in zip(dist_df["label"], dist_df["n"])]
    neg1, neg2 = -dist_df[1], -dist_df[2]
    neg3, pos3 = -dist_df[3] / 2, dist_df[3] / 2
    pos4, pos5 = dist_df[4], dist_df[5]

    def bar(x, val, show_legend):
        return go.Bar(
            y=labels, x=x, orientation="h", name=LIKERT_LABELS[val],
            marker_color=LIKERT_COLORS[val], showlegend=show_legend,
            customdata=x.abs(),
            hovertemplate="%{y}<br>" + LIKERT_LABELS[val] + ": <b>%{customdata:.0f}%</b><extra></extra>",
        )

    fig = go.Figure()
    fig.add_trace(bar(neg1, 1, True))
    fig.add_trace(bar(neg2, 2, True))
    fig.add_trace(bar(neg3, 3, True))
    fig.add_trace(bar(pos3, 3, False))
    fig.add_trace(bar(pos4, 4, True))
    fig.add_trace(bar(pos5, 5, True))
    fig.update_layout(
        barmode="relative",
        xaxis=dict(title="% of responses", ticksuffix="%", range=[-100, 100], gridcolor=PALETTE["grid"]),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        height=90 + 40 * len(dist_df), margin=dict(t=40, b=10, l=10, r=10),
        plot_bgcolor="white",
    )
    st.plotly_chart(fig, width="stretch")


def categorical_distribution(filt_resp, spec):
    """% breakdown for one non-1-5 question (Yes/No/Not Sure, a worded
    confidence scale, ...), computed over the currently filtered responses.
    Returns None if there's no usable data yet."""
    if filt_resp.empty or "special" not in filt_resp.columns:
        return None
    aliases = spec.get("value_aliases", {})
    vals = filt_resp["special"].map(lambda d: d.get(spec["code"]))
    vals = vals.map(lambda v: aliases.get(v, v))  # fold known typo'd/legacy answers into the canonical label
    vals = vals[vals.isin(spec["options"])]  # drop blanks and any still-unrecognized answer
    n = len(vals)
    if n == 0:
        return None
    counts = vals.value_counts()
    pct = {opt: 100 * counts.get(opt, 0) / n for opt in spec["options"]}
    return {"pct": pct, "n": n}


def categorical_question_chart(filt_resp, spec):
    """Single-row 100%-stacked horizontal bar for one categorical question —
    e.g. Yes/No/Not Sure, or a worded confidence scale. Kept as its own chart
    type (rather than folded into distribution_chart) because each of these
    questions has its own option set and cardinality, unlike the uniform
    5-point Likert questions."""
    dist = categorical_distribution(filt_resp, spec)
    if dist is None:
        st.info("No responses yet.")
        return
    pct = dist["pct"]
    label = f"{spec['short_label']}  (n={dist['n']})"
    fig = go.Figure()
    for opt in spec["options"]:
        v = pct[opt]
        fig.add_bar(
            y=[label], x=[v], orientation="h", name=opt,
            marker_color=spec["colors"][opt],
            text=f"{v:.0f}%" if v >= 8 else "",  # skip the label on slivers too thin to hold it
            textposition="inside", insidetextanchor="middle",
            textfont=dict(color="white", size=12),
            hovertemplate=f"{opt}: <b>%{{x:.1f}}</b>%<extra></extra>",
        )
    fig.update_layout(
        barmode="stack",
        xaxis=dict(title="% of responses", ticksuffix="%", range=[0, 100], gridcolor=PALETTE["grid"]),
        yaxis=dict(showticklabels=False),
        legend=dict(orientation="h", yanchor="bottom", y=1.2, xanchor="left", x=0),
        height=170, margin=dict(t=55, b=30, l=10, r=10),
        plot_bgcolor="white",
    )
    st.plotly_chart(fig, width="stretch")


def render_special_questions(filt_resp, specs):
    """Renders one card per categorical (non-1-5) question, side by side.
    A no-op when the sheet has none defined (e.g. Technical, currently)."""
    if not specs:
        return
    section_header("❓", "Additional questions")
    cols = st.columns(len(specs))
    for col, spec in zip(cols, specs):
        with col:
            with st.container(border=True):
                st.markdown(f"**{spec['short_label']}**")
                categorical_question_chart(filt_resp, spec)


def render_action_items(items, cat1_label, cat2_label):
    section_header("⚠️", "Needs attention")
    if not items:
        st.success("Nothing below 3/5 in the current selection.")
        return
    a1, a2 = st.columns(2)
    for col, cat_lbl in [(a1, cat1_label), (a2, cat2_label)]:
        with col:
            st.markdown(f"**{cat_lbl}**")
            group = [i for i in items if i["Category"] == cat_lbl]
            if not group:
                st.caption("Nothing below 3/5 here.")
                continue
            df = pd.DataFrame(group)[["Area", "Item", "Avg Score", "Responses"]]
            df["Avg Score"] = df["Avg Score"].map(lambda v: f"{v:.2f}")
            st.dataframe(df, width="stretch", hide_index=True)
    low_n = [i for i in items if i["Responses"] < 3]
    if low_n:
        st.caption(
            f"⚠️ {len(low_n)} of these items have fewer than 3 responses — treat as early signal, "
            "not a settled result, until more feedback comes in."
        )


def render_wrapped_table(df):
    """Renders a DataFrame as a plain HTML table with wrapped cell text —
    used where free-text answers are long enough that st.dataframe's
    single-line truncation would hide most of the content."""
    if df.empty:
        return
    header_html = "".join(f"<th>{html.escape(str(c))}</th>" for c in df.columns)
    body_rows = []
    for _, row in df.iterrows():
        cells = "".join(f"<td>{html.escape('' if pd.isna(v) else str(v))}</td>" for v in row)
        body_rows.append(f"<tr>{cells}</tr>")
    st.markdown(
        f'<div class="wrapped-table-container"><table class="wrapped-table">'
        f'<thead><tr>{header_html}</tr></thead><tbody>{"".join(body_rows)}</tbody>'
        f'</table></div>',
        unsafe_allow_html=True,
    )


def render_comments_panel(filt_resp, key_prefix):
    has_comments = filt_resp["comments"].map(lambda d: len(d) > 0).any() if "comments" in filt_resp.columns else False
    if not has_comments:
        return
    section_header("💬", "Student comments")
    only_low = st.checkbox(
        "Only show responses needing attention (overall score below 3/5)",
        key=f"{key_prefix}_comments_low",
    )
    rows = filt_resp[filt_resp["avg"] < 3] if only_low else filt_resp
    rows = rows[rows["comments"].map(lambda d: len(d) > 0)]
    if rows.empty:
        st.caption("No comments in the current selection.")
        return
    display_rows = []
    for _, r in rows.sort_values("date", ascending=False).iterrows():
        row = {"Date": r["date"], "Centre": r["centre"], "Mentor": r["mentor"], "Overall": f"{r['avg']:.2f}"}
        row.update(r["comments"])
        display_rows.append(row)
    # A plain HTML table instead of st.dataframe: st.dataframe truncates long
    # cells to a single line with no way to force wrapping, which was cutting
    # off free-text answers (Suggestions especially) instead of showing them.
    render_wrapped_table(pd.DataFrame(display_rows))


# ---------------------------------------------------------------------------
# Dashboard renderer (shared by both sheets)
# ---------------------------------------------------------------------------

def render_dashboard(key_prefix, endpoint_key, form_name, cat1_label, cat2_label, qmap,
                      centre_col, mentor_col, course_col, batch_col, comment_cols=None, special_cols=None):
    try:
        raw_df, fetched_at = load_sheet(endpoint_key)
    except KeyError:
        st.info(
            f"The {form_name} data endpoint isn't set up in secrets.toml yet "
            f"(missing `sheet_endpoints.{endpoint_key}`). Add it once you've "
            f"deployed that sheet's Apps Script — see SETUP_GUIDE.md."
        )
        return
    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
        st.error(
            f"Couldn't reach the {form_name} data source after a few tries — this is usually "
            "temporary (a slow response from Google, or a network hiccup)."
        )
        if st.button("Try again", key=f"{key_prefix}_retry"):
            st.cache_data.clear()
            st.rerun()
        return
    except Exception as e:
        st.error(f"Couldn't load {form_name} data: {e}")
        return
    st.caption(f"📅 Data as of {fetched_at.strftime('%b %d, %Y, %I:%M %p')}")

    response_df, question_df = build_response_and_question_df(
        raw_df, centre_col, mentor_col, course_col, batch_col, qmap,
        comment_cols=comment_cols, special_cols=special_cols
    )

    if response_df.empty:
        st.info(f"No responses yet in the {form_name} sheet. This dashboard will populate automatically once students start submitting the form.")
        return

    # Filters
    fcol1, fcol2, fcol3 = st.columns([2, 2, 1])
    centres = sorted(response_df["centre"].dropna().unique().tolist())
    with fcol1:
        # A link like "...?centre=Delhi" pre-selects that centre — this is what
        # lets the weekly per-centre email point each centre straight at its
        # own filtered view instead of the all-centres dashboard. Only affects
        # the widget's *initial* value: once a viewer picks a different centre
        # themselves, their selection (tracked by `key`) takes over as usual.
        centre_options = ["All Learning Centres"] + centres
        query_centre = st.query_params.get("centre")
        default_centre_index = centre_options.index(query_centre) if query_centre in centre_options else 0
        centre_sel = st.selectbox(
            "Learning Centre", centre_options, index=default_centre_index, key=f"{key_prefix}_centre"
        )

    mentor_options = ["All Mentors"]
    mentor_lookup = {}
    for m in sorted(response_df["mentor"].dropna().unique().tolist()):
        centres_for_m = sorted(response_df.loc[response_df["mentor"] == m, "centre"].dropna().unique().tolist())
        label = f"{m} — {', '.join(centres_for_m)}" if centres_for_m else m
        mentor_options.append(label)
        mentor_lookup[label] = m
    with fcol2:
        mentor_sel_label = st.selectbox("Mentor / Faculty", mentor_options, key=f"{key_prefix}_mentor")
    mentor_sel = mentor_lookup.get(mentor_sel_label)

    with fcol3:
        st.write("")
        if st.button("Refresh data", key=f"{key_prefix}_refresh"):
            st.cache_data.clear()
            st.rerun()

    filt_resp = response_df.copy()
    filt_q = question_df.copy()
    if centre_sel != "All Learning Centres":
        filt_resp = filt_resp[filt_resp["centre"] == centre_sel]
        filt_q = filt_q[filt_q["centre"] == centre_sel]
    if mentor_sel:
        filt_resp = filt_resp[filt_resp["mentor"] == mentor_sel]
        filt_q = filt_q[filt_q["mentor"] == mentor_sel]

    overall = filt_resp["avg"].mean() if not filt_resp.empty else None
    cat1_avg = filt_resp["cat1"].mean() if not filt_resp.empty else None
    cat2_avg = filt_resp["cat2"].mean() if not filt_resp.empty else None
    kpi_row(overall, cat1_avg, cat2_avg, len(filt_resp), cat1_label, cat2_label)

    render_action_items(build_action_items(filt_resp, filt_q, cat1_label, cat2_label), cat1_label, cat2_label)

    section_header("📈", "Average score over time")
    with st.container(border=True):
        trend_chart(trend_by_date(filt_resp))

    section_header("🧭", "Breakdowns")
    b1, b2, b3 = st.columns(3)
    with b1:
        with st.container(border=True):
            st.markdown("**By Learning Centre**")
            grouped_breakdown_chart(group_avg(filt_resp, "centre"), cat1_label, cat2_label)
    with b2:
        with st.container(border=True):
            st.markdown("**By Mentor / Faculty**")
            grouped_breakdown_chart(group_avg(filt_resp, "mentor"), cat1_label, cat2_label)
    with b3:
        with st.container(border=True):
            st.markdown("**By Course**")
            grouped_breakdown_chart(group_avg(filt_resp, "course"), cat1_label, cat2_label)

    section_header("🎯", "Question breakdown, weakest to strongest")
    q1, q2 = st.columns(2)
    with q1:
        with st.container(border=True):
            st.markdown(f"**{cat1_label} questions**")
            question_chart(question_ranking(filt_q, "cat1"), PALETTE["series1"], cat1_label)
    with q2:
        with st.container(border=True):
            st.markdown(f"**{cat2_label} questions**")
            question_chart(question_ranking(filt_q, "cat2"), PALETTE["series2"], cat2_label)

    section_header("📊", "Response spread per question")
    st.caption("An average can hide a split opinion — this shows the actual mix of ratings, not just the mean.")
    d1, d2 = st.columns(2)
    with d1:
        with st.container(border=True):
            st.markdown(f"**{cat1_label} questions**")
            distribution_chart(question_distribution(filt_q, "cat1"))
    with d2:
        with st.container(border=True):
            st.markdown(f"**{cat2_label} questions**")
            distribution_chart(question_distribution(filt_q, "cat2"))

    section_header("🔥", "Performance by question")
    st.caption("Where a mentor's blended average is hiding one specific weak spot.")
    h1, h2 = st.columns(2)
    with h1:
        with st.container(border=True):
            st.markdown(f"**{cat1_label} questions**")
            matrix, code_text = mentor_question_matrix(filt_q, "cat1", qmap)
            heatmap_chart(matrix, code_text)
    with h2:
        with st.container(border=True):
            st.markdown(f"**{cat2_label} questions**")
            matrix, code_text = mentor_question_matrix(filt_q, "cat2", qmap)
            heatmap_chart(matrix, code_text)

    render_special_questions(filt_resp, special_cols)

    render_comments_panel(filt_resp, key_prefix)

    st.caption(f"Data refreshes automatically every {CACHE_TTL_SECONDS // 60} minutes, or click \"Refresh data\" above for an immediate pull.")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

def main():
    st.markdown(
        """
        <div class="hero-banner">
            <h1>🎓 Jetking Feedback Dashboards</h1>
            <p>Live from the Technical and Employability Session Feedback Google Sheets.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    tab1, tab2 = st.tabs(["Technical Session Feedback", "Employability Session Feedback"])

    with tab1:
        render_dashboard(
            key_prefix="tech",
            endpoint_key="technical_url",
            form_name="Technical Session Feedback",
            cat1_label="Session Delivery Avg",
            cat2_label="Mentor Behavior Avg",
            qmap=TECH_Q,
            centre_col="Jetking Learning Centre Name",
            mentor_col="Mentor Name",
            course_col="Course Name",
            batch_col="Batch Code",
            comment_cols=TECH_COMMENT_COLS,
            special_cols=TECH_SPECIAL_Q,
        )

    with tab2:
        render_dashboard(
            key_prefix="emp",
            endpoint_key="employability_url",
            form_name="Employability Session Feedback",
            cat1_label="Session Content Avg",
            cat2_label="Mentor Behavior Avg",
            qmap=EMP_Q,
            centre_col="Jetking Learning Centre Name",
            mentor_col="Employability Mentor Name (Please select from the dropdown)",
            course_col="Course Name",
            batch_col="Batch Code (eg. 2627-JU-1234)",
            comment_cols=EMP_COMMENT_COLS,
            special_cols=EMP_SPECIAL_Q,
        )


if __name__ == "__main__":
    main()
