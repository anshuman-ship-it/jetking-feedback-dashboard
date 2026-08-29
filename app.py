"""
Jetking Feedback Dashboards — Streamlit app
Live-connects to the Technical Session Feedback, Employability Session
Feedback, and Centre Infrastructure Feedback Google Sheets via each sheet's
"Publish to web" CSV link (no Google Cloud project, service account, or Apps
Script needed). The Technical and Employability tabs render KPIs, trend over
time, breakdowns by Centre / Mentor / Course & Batch (split into two question
categories), and a weakest-to-strongest question ranking per category, with
Centre and Mentor filters combining. The Infrastructure tab has no Mentor
dimension (it's about facilities, not a person) — see
render_infrastructure_dashboard() for how it differs.

Setup: see SETUP_GUIDE.md in this folder.
"""

import html
import io
import time
from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

import streamlit as st
import streamlit_authenticator as stauth
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

# Dark mode: chrome (backgrounds, borders, ink) is theme-aware; the data-encoding
# colors above (PALETTE, LIKERT_COLORS, HEATMAP_COLORSCALE further down) are
# deliberately NOT re-picked per theme — the same hue always means the same
# thing in both modes, only the page/card/chart background and text around it
# changes. Native Streamlit widgets (tabs, selectbox, buttons, dataframe) are
# handled separately by .streamlit/config.toml's [theme.light]/[theme.dark]
# tables — this dict only covers the custom HTML/CSS and Plotly chrome below.
THEMES = {
    "light": {
        "surface": "#ffffff",
        "text": "#0b0b0b",
        "muted": PALETTE["muted"],
        "border": PALETTE["grid"],
        "table_stripe": "#fafaf8",
        "table_header": f'{PALETTE["grid"]}55',
        "hero_grad": (PALETTE["series1"], "#184f95"),
        "chart_bg": "#ffffff",
        "chart_font": "#33322e",
        "chart_grid": PALETTE["grid"],
        "kpi_neutral_bg": "#ffffff",
        "kpi_neutral_text": "#0b0b0b",
        "kpi_blue_bg": "#eaf2fc", "kpi_blue_border": PALETTE["series1"], "kpi_blue_text": "#184f95",
        "kpi_yellow_bg": "#fff6e0", "kpi_yellow_border": "#c98500", "kpi_yellow_text": "#7a5200",
        "kpi_red_bg": "#fceceb", "kpi_red_border": PALETTE["serious"], "kpi_red_text": "#8a1f1f",
    },
    "dark": {
        "surface": "#1b232c",
        "text": "#eef1f5",
        "muted": "#9aa4b1",
        "border": "#2c3946",
        "table_stripe": "#20293380",
        "table_header": "#2c394680",
        "hero_grad": ("#12895e", "#123a6e"),
        "chart_bg": "#1b232c",
        "chart_font": "#c7ced7",
        "chart_grid": "#33404d",
        "kpi_neutral_bg": "#1b232c",
        "kpi_neutral_text": "#eef1f5",
        "kpi_blue_bg": "#15304f", "kpi_blue_border": "#4f9eea", "kpi_blue_text": "#bfe0ff",
        "kpi_yellow_bg": "#463710", "kpi_yellow_border": "#e0a83c", "kpi_yellow_text": "#ffdf9e",
        "kpi_red_bg": "#451e1e", "kpi_red_border": "#e2635f", "kpi_red_text": "#ffd0cc",
    },
}


def _detect_theme_type():
    """Reads the viewer's current Streamlit theme (native Settings menu ->
    Theme -> Light/Dark), so our custom HTML/CSS and Plotly charts can match
    it. Falls back to "light" on older Streamlit versions that predate
    st.context.theme (added in 1.51) or if the value is momentarily unset
    right as a viewer switches themes."""
    try:
        t = st.context.theme.type
        if t in ("light", "dark"):
            return t
    except Exception:
        pass
    return "light"


DARK_MODE = _detect_theme_type() == "dark"
THEME = THEMES["dark" if DARK_MODE else "light"]

# Page chrome: hero banner, elevated KPI cards color-coded per metric, section
# headers with an icon + accent rule, and lightly polished tabs/tables/buttons.
# Purely cosmetic — no effect on data or layout logic below.
st.markdown(f"""
<style>
.hero-banner {{
    background: linear-gradient(135deg, {THEME["hero_grad"][0]} 0%, {THEME["hero_grad"][1]} 100%);
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
    background: {THEME["surface"]};
    border-radius: 12px;
    padding: 1.1rem 0.5rem 1rem;
    box-shadow: 0 1px 3px rgba(11,11,11,0.08), 0 1px 2px rgba(11,11,11,0.05);
    border: 1px solid {THEME["border"]};
    border-top: 4px solid {PALETTE["series1"]};
    text-align: center;
    transition: box-shadow 0.15s ease;
}}
.kpi-card:hover {{
    box-shadow: 0 4px 14px rgba(11,11,11,0.12);
}}
.kpi-label {{
    font-size: 0.875rem;
    color: {THEME["muted"]};
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
    border: 1px solid {THEME["border"]};
    margin-bottom: 0.5rem;
    background: {THEME["surface"]};
}}
.wrapped-table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 0.9rem;
    color: {THEME["text"]};
}}
.wrapped-table th {{
    background: {THEME["table_header"]};
    text-align: left;
    padding: 0.55rem 0.75rem;
    font-weight: 700;
    border-bottom: 2px solid {THEME["border"]};
    white-space: nowrap;
}}
.wrapped-table td {{
    padding: 0.55rem 0.75rem;
    border-bottom: 1px solid {THEME["border"]};
    white-space: normal;
    overflow-wrap: break-word;
    min-width: 120px;  /* a short value (a name, a city) never gets squeezed into a mid-word break */
    vertical-align: top;
}}
.wrapped-table tbody tr:nth-child(even) {{ background: {THEME["table_stripe"]}; }}

/* Custom chart legend — used INSTEAD of Plotly's own built-in legend on every
   chart that has one. Plotly's horizontal legend doesn't wrap: on a narrow
   (mobile) screen its items just squeeze into the available width and their
   text overlaps. A plain flex-wrap row never does that — items simply drop
   to a second line once they run out of horizontal room, on any screen size. */
.chart-legend {{
    display: flex;
    flex-wrap: wrap;
    gap: 0.3rem 1rem;
    margin: 0.1rem 0 0.6rem 0;
    font-size: 0.82rem;
    color: {THEME["muted"]};
}}
.chart-legend .legend-chip {{
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
    white-space: nowrap;
}}
.chart-legend .legend-swatch {{
    display: inline-block;
    width: 10px;
    height: 10px;
    border-radius: 3px;
    flex-shrink: 0;
}}

.section-header {{
    display: flex;
    align-items: center;
    gap: 0.55rem;
    margin: 1.9rem 0 0.85rem 0;
    padding-bottom: 0.45rem;
    border-bottom: 2px solid {THEME["border"]};
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

/* Phone-width tightening — the layout above already reflows fine (Streamlit
   stacks columns automatically below ~640px), this just scales down a few
   elements that are sized for desktop by default so nothing feels oversized
   or cramped on a small screen. */
@media (max-width: 480px) {{
    .hero-banner {{ padding: 1.1rem 1.25rem; }}
    .hero-banner h1 {{ font-size: 1.5rem; }}
    .hero-banner p {{ font-size: 0.85rem; }}
    .kpi-value {{ font-size: 1.5rem; }}
    .section-header h3 {{ font-size: 1.1rem; }}
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


def render_chart_legend(items):
    """items: list of (label, hex_color). Renders a wrapping HTML legend in
    place of Plotly's built-in one — see the .chart-legend CSS comment above
    for why (mobile overlap). Call this right before st.plotly_chart() on a
    figure that has showlegend=False."""
    chips = "".join(
        f'<span class="legend-chip"><span class="legend-swatch" style="background:{color};"></span>{html.escape(str(label))}</span>'
        for label, color in items
    )
    st.markdown(f'<div class="chart-legend">{chips}</div>', unsafe_allow_html=True)

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

# Centre Infrastructure Feedback Form: a fundamentally different shape from
# the other two — no Mentor dimension (it's about facilities, not a person),
# and only 2 Likert questions total, one per "category" here (so cat1/cat2
# each represent a single facility question rather than a multi-question
# theme — the KPI/breakdown machinery doesn't care, it just averages whatever
# is tagged cat1 vs cat2). Most of the real signal is in the 6 Yes/No /
# Yes/No/Not Sure questions below, rendered via the same "Additional
# questions" panel used for Employability's categorical questions.
INFRA_Q = [
    ("F1", "Rate the Drinking water facility available at the Center?", "Drinking water facility", "cat1"),
    ("F2", "Rate the washroom facility available at the center?", "Washroom facility", "cat2"),
]

INFRA_COMMENT_COLS = [
    ("Please provide suggestions for improvements (if any):", "Suggestions"),
]

INFRA_SPECIAL_Q = [
    {
        "code": "I1",
        "column": "Does the center provide separate washroom facilities for male and female students?",
        "short_label": "Separate washrooms (M/F)",
        "options": ["No", "Yes"],
        "colors": {"No": PALETTE["serious"], "Yes": "#0ca30c"},
    },
    {
        "code": "I2",
        "column": "Do you have access to a dedicated PC for doing practical's (1:1 student-to-PC ratio)?",
        "short_label": "1:1 dedicated PC access",
        "options": ["No", "Yes"],
        "colors": {"No": PALETTE["serious"], "Yes": "#0ca30c"},
    },
    {
        "code": "I3",
        "column": "Are the necessary tools and components provided during the practical sessions?",
        "short_label": "Tools/components provided",
        "options": ["No", "Yes"],
        "colors": {"No": PALETTE["serious"], "Yes": "#0ca30c"},
    },
    {
        "code": "I4",
        "column": "Are the PC's having the configuration Intel i5 Processor, 7th Generation, 16GB RAM & Storage of 1TB?",
        "short_label": "PC spec: i5 7th gen / 16GB / 1TB",
        "options": ["No", "Not Sure", "Yes"],
        "colors": {"No": PALETTE["serious"], "Not Sure": PALETTE["muted"], "Yes": "#0ca30c"},
    },
    {
        "code": "I5",
        "column": "Is the lab equipped with the necessary infrastructure (e.g., chairs, tables, TV Monitor, and AC's)?",
        "short_label": "Lab infra (chairs/tables/TV/AC)",
        "options": ["No", "Yes"],
        "colors": {"No": PALETTE["serious"], "Yes": "#0ca30c"},
    },
    {
        "code": "I6",
        "column": "Are the lab PCs equipped with the necessary operating system and required software?",
        "short_label": "OS & required software",
        "options": ["No", "Not Sure", "Yes"],
        "colors": {"No": PALETTE["serious"], "Not Sure": PALETTE["muted"], "Yes": "#0ca30c"},
    },
]


# ---------------------------------------------------------------------------
# Centre name canonicalization
# ---------------------------------------------------------------------------
#
# The "Jetking Learning Centre Name" field has already drifted across sheets
# once (Technical says "...Learning Center", Employability says "...Training
# Center" for the same physical Khar centre) — see dashboard-build-notes for
# the full history. Rather than matching on the raw string (which a typo or a
# form edit can silently break), every raw centre value is canonicalized to
# one of the 6 known centres by keyword match, tolerant of "Learning" vs
# "Training", "Jetking" vs "JK", spacing, etc. This also underpins role-based
# access below: a centre login is locked to one of these 6 canonical keys.
CENTRE_KEYWORDS = {
    "Dadar": "dadar",
    "Vashi": "vashi",
    "Laxminagar": "laxmi",
    "Maninagar": "manin",
    "Bhawaniopore": "bhawani",
    "Khar": "khar",
}

CENTRE_DISPLAY_NAMES = {
    "Dadar": "Dadar Learning Centre",
    "Vashi": "Vashi Learning Centre",
    "Laxminagar": "Laxminagar Learning Centre",
    "Maninagar": "Maninagar Learning Centre",
    "Bhawaniopore": "Bhawaniopore Learning Centre",
    "Khar": "Khar Learning Centre",
}

# Accounts allowed to download the current filtered view as raw sheet rows
# (see render_raw_download_button()) — everyone else's view is unchanged.
# Add an email here to extend this to someone else; no other code to touch.
RAW_DOWNLOAD_EMAILS = {"dhruti@jetking.com"}


def canonicalize_centre(raw_value):
    """Maps a raw centre string to one of the 6 known canonical centre keys
    by keyword match (case-insensitive substring). Returns None if it
    doesn't match any known centre — a new centre or a typo bad enough that
    even the keyword didn't match; callers surface this rather than
    silently dropping or misfiling the response."""
    if not raw_value:
        return None
    v = str(raw_value).lower()
    for canonical, keyword in CENTRE_KEYWORDS.items():
        if keyword in v:
            return canonical
    return None


def normalize_centre_display(raw_value):
    """The display string stored as a response's "centre" field throughout
    the app — the canonical "<Name> Learning Centre" for a recognized
    centre, or a visibly-flagged "Unrecognized centre (...)" bucket so a new
    or badly-misspelled centre shows up as something to investigate instead
    of quietly becoming its own confusing dropdown entry."""
    key = canonicalize_centre(raw_value)
    if key:
        return CENTRE_DISPLAY_NAMES[key]
    raw = str(raw_value).strip()
    return f"Unrecognized centre ({raw})" if raw else ""


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
    mentor_col: pass None for a sheet with no mentor/faculty dimension at all (e.g. the
    Infrastructure form, which is about facilities rather than a person) — every response's
    "mentor" is then just "", and callers should skip mentor-specific UI (filter, breakdown,
    heatmap) entirely rather than showing an always-blank one.
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
        centre = normalize_centre_display(r.get(centre_col, ""))
        mentor = str(r.get(mentor_col, "")).strip() if mentor_col else ""
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
            "_raw_index": i,  # position in the raw sheet dataframe — lets a caller
                              # (see allow_raw_download in render_dashboard()) map a
                              # filtered response back to its original, unmodified row
                              # for a "download the underlying sheet rows" export.
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


def build_action_items(filt_resp, filt_q, cat1_label, cat2_label, threshold=3.0, scopes=None):
    """Consolidated list of every mentor/centre/course/question currently below
    threshold, worst first, each tagged with its response count and which of
    the two question categories it belongs to.
    scopes: optional list of (display label, key_col) pairs to break down by — defaults to
    Centre/Mentor/Course. Pass a custom list for a sheet with no mentor dimension (e.g.
    Infrastructure), omitting the Mentor/Faculty scope entirely."""
    items = []
    scopes = scopes if scopes is not None else [("Learning Centre", "centre"), ("Mentor/Faculty", "mentor"), ("Course", "course")]
    for scope, key_col in scopes:
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
    None (no responses yet, or not a 1-5 score at all) stays neutral gray.
    Colors come from THEME so they stay legible in both light and dark mode."""
    if score is None:
        return {"bg": THEME["kpi_neutral_bg"], "border": THEME["muted"], "text": THEME["kpi_neutral_text"]}
    if score > 3.5:
        return {"bg": THEME["kpi_blue_bg"], "border": THEME["kpi_blue_border"], "text": THEME["kpi_blue_text"]}
    elif score >= 2.5:
        return {"bg": THEME["kpi_yellow_bg"], "border": THEME["kpi_yellow_border"], "text": THEME["kpi_yellow_text"]}
    else:
        return {"bg": THEME["kpi_red_bg"], "border": THEME["kpi_red_border"], "text": THEME["kpi_red_text"]}


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
        x=dates, y=trend_df["avg"], mode="lines+markers+text",
        line=dict(color=PALETTE["series1"], width=2),
        marker=dict(size=7, color=PALETTE["series1"]),
        fill="tozeroy", fillcolor="rgba(42,120,214,0.10)",
        text=[f"{v:.2f}" for v in trend_df["avg"]],
        textposition="top center",
        textfont=dict(size=11, color=THEME["chart_font"]),
        cliponaxis=False,  # so a label on a point near the top of the 0-5 range doesn't get clipped
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
    xaxis_kwargs = dict(
        type="date", tickformat="%b %d, %Y", range=x_range, gridcolor=THEME["chart_grid"],
        tickfont=dict(color=THEME["chart_font"]),
    )
    if dtick:
        xaxis_kwargs["dtick"] = dtick
    fig.update_layout(
        yaxis=dict(range=[0, 5], gridcolor=THEME["chart_grid"], tickfont=dict(color=THEME["chart_font"])),
        xaxis=xaxis_kwargs,
        height=280, margin=dict(t=28, b=10, l=10, r=10),  # extra top margin so a data label near the top isn't clipped
        plot_bgcolor=THEME["chart_bg"], paper_bgcolor=THEME["chart_bg"],
        font=dict(color=THEME["chart_font"]), showlegend=False,
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
        barmode="group",
        xaxis=dict(range=[0, 5], gridcolor=THEME["chart_grid"], automargin=True, tickfont=dict(color=THEME["chart_font"])),
        yaxis=dict(automargin=True, tickfont=dict(color=THEME["chart_font"])),
        showlegend=False,
        height=110 + 55 * len(df_group), margin=dict(t=10, b=10, l=10, r=10),
        plot_bgcolor=THEME["chart_bg"], paper_bgcolor=THEME["chart_bg"],
        font=dict(color=THEME["chart_font"]),
    )
    render_chart_legend([(cat1_label, PALETTE["series1"]), (cat2_label, PALETTE["series2"])])
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
        textposition="outside", textfont=dict(color=THEME["chart_font"]),
        cliponaxis=False,  # otherwise labels near the right edge get cut off
        hovertemplate="%{y}<br><b>%{x:.2f}</b> / 5<extra></extra>",
    )
    fig.update_layout(
        xaxis=dict(range=[0, 6], gridcolor=THEME["chart_grid"], automargin=True, tickfont=dict(color=THEME["chart_font"])),
        height=80 + 40 * len(qrank_df), margin=dict(t=10, b=10, l=10, r=40),
        plot_bgcolor=THEME["chart_bg"], paper_bgcolor=THEME["chart_bg"],
        font=dict(color=THEME["chart_font"]), showlegend=False,
        yaxis=dict(autorange="reversed", automargin=True, tickfont=dict(color=THEME["chart_font"])),
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
        colorbar=dict(
            title=dict(text="Avg", font=dict(color=THEME["chart_font"])),
            tickvals=[1, 2, 3, 4, 5], tickfont=dict(color=THEME["chart_font"]),
            lenmode="pixels", len=140, thickness=14,
        ),
        xgap=2, ygap=2,
    ))
    # Floor the height so a short heatmap (few mentors) still leaves the
    # colorbar's 5 tick labels enough room not to overlap.
    fig.update_layout(
        height=max(220, 80 + 36 * len(matrix_df.index)), margin=dict(t=30, b=10, l=10, r=10),
        plot_bgcolor=THEME["chart_bg"], paper_bgcolor=THEME["chart_bg"],
        font=dict(color=THEME["chart_font"]),
        xaxis=dict(
            side="top", automargin=True, tickfont=dict(color=THEME["chart_font"]),
            title=dict(text="Question code", font=dict(color=THEME["chart_font"])),
        ),
        yaxis=dict(automargin=True, tickfont=dict(color=THEME["chart_font"])),
    )
    st.plotly_chart(fig, width="stretch")
    st.caption("Hover a cell to see the full question.")


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
        xaxis=dict(
            title=dict(text="% of responses", font=dict(color=THEME["chart_font"])),
            ticksuffix="%", range=[-100, 100], gridcolor=THEME["chart_grid"],
            tickfont=dict(color=THEME["chart_font"]),
        ),
        yaxis=dict(automargin=True, tickfont=dict(color=THEME["chart_font"])),
        showlegend=False,
        height=90 + 40 * len(dist_df), margin=dict(t=10, b=10, l=10, r=10),
        plot_bgcolor=THEME["chart_bg"], paper_bgcolor=THEME["chart_bg"],
        font=dict(color=THEME["chart_font"]),
    )
    render_chart_legend([(LIKERT_LABELS[v], LIKERT_COLORS[v]) for v in (1, 2, 3, 4, 5)])
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
        xaxis=dict(
            title=dict(text="% of responses", font=dict(color=THEME["chart_font"])),
            ticksuffix="%", range=[0, 100], gridcolor=THEME["chart_grid"],
            tickfont=dict(color=THEME["chart_font"]),
        ),
        yaxis=dict(showticklabels=False),
        showlegend=False,
        height=130, margin=dict(t=10, b=30, l=10, r=10),
        plot_bgcolor=THEME["chart_bg"], paper_bgcolor=THEME["chart_bg"],
        font=dict(color=THEME["chart_font"]),
    )
    render_chart_legend([(opt, spec["colors"][opt]) for opt in spec["options"]])
    st.plotly_chart(fig, width="stretch")


def render_special_questions(filt_resp, specs, cols_per_row=None):
    """Renders one card per categorical (non-1-5) question, in rows of
    `cols_per_row` columns (default: all in a single row, as before — fine
    for a handful of questions like Employability's 3, but the Infrastructure
    form has 6, which need to wrap into two rows to stay readable).
    A no-op when the sheet has none defined (e.g. Technical, currently)."""
    if not specs:
        return
    section_header("❓", "Additional questions")
    cols_per_row = cols_per_row or len(specs)
    for i in range(0, len(specs), cols_per_row):
        row_specs = specs[i:i + cols_per_row]
        cols = st.columns(len(row_specs))
        for col, spec in zip(cols, row_specs):
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


def render_comments_panel(filt_resp, key_prefix, extra_cols=None):
    """extra_cols: optional list of (display label, response_df key) pairs shown between
    Date and Overall — defaults to Centre + Mentor. Pass a custom list for a sheet with no
    mentor dimension (e.g. Infrastructure), e.g. [("Centre", "centre"), ("Course", "course")]."""
    has_comments = filt_resp["comments"].map(lambda d: len(d) > 0).any() if "comments" in filt_resp.columns else False
    if not has_comments:
        return
    extra_cols = extra_cols if extra_cols is not None else [("Centre", "centre"), ("Mentor", "mentor")]
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
        row = {"Date": r["date"]}
        for label, key in extra_cols:
            row[label] = r[key]
        row["Overall"] = f"{r['avg']:.2f}"
        row.update(r["comments"])
        display_rows.append(row)
    display_df = pd.DataFrame(display_rows)
    # A plain HTML table instead of st.dataframe: st.dataframe truncates long
    # cells to a single line with no way to force wrapping, which was cutting
    # off free-text answers (Suggestions especially) instead of showing them.
    render_wrapped_table(display_df)
    st.download_button(
        "⬇️ Download comments (CSV)",
        data=display_df.to_csv(index=False).encode("utf-8"),
        file_name=f"{key_prefix}_comments.csv",
        mime="text/csv",
        key=f"{key_prefix}_comments_download",
        help="Exactly the comments shown in the table above — same filters and "
             "the 'needing attention' checkbox, if you've ticked it.",
    )


def render_raw_download_button(raw_df, filt_resp, key_prefix, form_name):
    """Lets an authorized viewer download the ORIGINAL sheet rows (every
    column exactly as submitted, no canonicalization/rounding/relabeling)
    for whatever the current filters have narrowed the view down to.
    filt_resp's "_raw_index" column (set in build_response_and_question_df)
    maps each filtered response back to its row position in raw_df, so this
    always mirrors the same set of responses currently on screen — never all
    of raw_df, and never the app's cleaned-up version of the data.
    Gated by an `allow_raw_download` flag per render call (see main()) —
    currently only dhruti@jetking.com has this enabled."""
    if filt_resp.empty:
        return
    raw_subset = raw_df.loc[filt_resp["_raw_index"]]
    csv_bytes = raw_subset.to_csv(index=False).encode("utf-8")
    st.download_button(
        "⬇️ Download filtered data (raw sheet rows, CSV)",
        data=csv_bytes,
        file_name=f"{key_prefix}_filtered_raw.csv",
        mime="text/csv",
        key=f"{key_prefix}_download_raw",
        help=f"Every original column from the {form_name} sheet, for exactly the "
             f"{len(raw_subset)} response(s) matching your current filters above.",
    )


def render_centre_filter(key_prefix, centre_options):
    """Learning Centre selectbox for a non-centre-locked view (admin), kept in
    sync across all three tabs: picking a centre in any one tab's dropdown
    updates the others too, via a shared `global_centre_sel` session_state
    slot written by every tab's on_change callback. Since Streamlit renders
    all three tab panels every run regardless of which is visually active,
    a change in one tab's widget updates global_centre_sel in time for the
    other two tabs (rendered right after it in main()) to pick it up the
    same run — no extra rerun needed.
    Falls back gracefully if the shared centre isn't present in this tab's
    own data (e.g. Infrastructure has fewer responses than Technical) — that
    tab just keeps showing whatever it already had rather than erroring.
    A "?centre=Delhi"-style query param (used by the weekly per-centre email
    links) still seeds the very first render, same as before this existed."""
    state_key = f"{key_prefix}_centre"
    shared = st.session_state.get("global_centre_sel")
    if state_key not in st.session_state:
        query_centre = st.query_params.get("centre")
        if shared in centre_options:
            st.session_state[state_key] = shared
        elif query_centre in centre_options:
            st.session_state[state_key] = query_centre
    elif shared in centre_options and st.session_state[state_key] != shared:
        st.session_state[state_key] = shared

    def _propagate_centre_selection():
        st.session_state["global_centre_sel"] = st.session_state[state_key]

    return st.selectbox(
        "Learning Centre", centre_options, key=state_key, on_change=_propagate_centre_selection
    )


# ---------------------------------------------------------------------------
# Dashboard renderer (shared by both sheets)
# ---------------------------------------------------------------------------

def render_dashboard(key_prefix, endpoint_key, form_name, cat1_label, cat2_label, qmap,
                      centre_col, mentor_col, course_col, batch_col, comment_cols=None, special_cols=None,
                      centre_lock=None, mentor_lock=None, allow_raw_download=False):
    """centre_lock: a list of one or more canonical centre keys (e.g.
    ["Khar"], from CENTRE_DISPLAY_NAMES) to restrict this render to — set
    for a centre-role login. A single-entry list locks to exactly one
    centre (the original centre-manager-style login: a static 🔒 label, no
    Centre filter dropdown, no "By Learning Centre" breakdown — both would
    be redundant with only one centre in scope). More than one entry
    restricts the Centre filter's options to just that subset instead, but
    otherwise behaves like the admin view (dropdown, breakdown chart,
    Centre column in comments — just scoped down to fewer centres). None
    (the default) shows every centre, as for an admin.
    mentor_lock: an exact mentor-name string (see resolve_role()) to further
    restrict this render to just that one mentor's own responses within
    whatever centre_lock already narrowed it to — a trainer-level login.
    Static 🔒 label instead of the Mentor/Faculty dropdown, no "By Mentor"
    breakdown or Mentor-scoped action items (both trivial with one mentor),
    no Mentor/Centre columns in the comments panel. None (the default)
    behaves exactly as before this existed.
    allow_raw_download: shows a button to download the current filtered view's
    underlying raw sheet rows as CSV — see render_raw_download_button()."""
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

    single_lock = bool(centre_lock) and len(centre_lock) == 1
    if centre_lock:
        locked_display_names = [CENTRE_DISPLAY_NAMES[c] for c in centre_lock]
        response_df = response_df[response_df["centre"].isin(locked_display_names)]
        question_df = question_df[question_df["centre"].isin(locked_display_names)]
        if response_df.empty:
            st.info(f"No responses yet for {', '.join(locked_display_names)} in the {form_name} sheet.")
            return

    mentor_locked = bool(mentor_lock)
    if mentor_locked:
        _mkey = mentor_lock.strip().casefold()
        response_df = response_df[response_df["mentor"].str.strip().str.casefold() == _mkey]
        question_df = question_df[question_df["mentor"].str.strip().str.casefold() == _mkey]
        if response_df.empty:
            st.info(f"No responses yet for {mentor_lock} in the {form_name} sheet.")
            return

    # Filters
    fcol1, fcol2, fcol3 = st.columns([2, 2, 1])
    if single_lock:
        centre_sel = locked_display_names[0]
        with fcol1:
            st.markdown("**Learning Centre**")
            st.markdown(f"🔒 {centre_sel}")
    elif centre_lock:
        with fcol1:
            centre_options = ["All My Centres"] + locked_display_names
            centre_sel = render_centre_filter(key_prefix, centre_options)
    else:
        centres = sorted(response_df["centre"].dropna().unique().tolist())
        with fcol1:
            centre_options = ["All Learning Centres"] + centres
            centre_sel = render_centre_filter(key_prefix, centre_options)

    if mentor_locked:
        with fcol2:
            st.markdown("**Mentor / Faculty**")
            st.markdown(f"🔒 {mentor_lock}")
        mentor_sel = mentor_lock
    else:
        # Scope which mentors appear to the currently selected centre, so picking
        # a centre narrows this dropdown too instead of always listing every
        # mentor across every centre. A single-centre-locked login never reaches
        # this branch's "all centres" case at all (response_df is already
        # limited to their one centre), so only the admin/multi-centre view
        # needed this fix.
        mentor_source_df = response_df
        if not single_lock and centre_sel not in ("All Learning Centres", "All My Centres"):
            mentor_source_df = response_df[response_df["centre"] == centre_sel]
        mentor_options = ["All Mentors"]
        mentor_lookup = {}
        for m in sorted(mentor_source_df["mentor"].dropna().unique().tolist()):
            # Still computed against the full (unscoped) response_df, not
            # mentor_source_df — so a mentor who also teaches at another centre
            # keeps showing that in the label even once a centre filter has
            # narrowed down which mentors are listed at all.
            centres_for_m = sorted(response_df.loc[response_df["mentor"] == m, "centre"].dropna().unique().tolist())
            label = f"{m} — {', '.join(centres_for_m)}" if centres_for_m else m
            mentor_options.append(label)
            mentor_lookup[label] = m
        mentor_key = f"{key_prefix}_mentor"
        if mentor_key in st.session_state and st.session_state[mentor_key] not in mentor_options:
            # The previously selected mentor isn't in this narrower centre's
            # list anymore — fall back to "All Mentors" rather than letting
            # Streamlit raise on a selection that's no longer a valid option.
            st.session_state[mentor_key] = "All Mentors"
        with fcol2:
            mentor_sel_label = st.selectbox("Mentor / Faculty", mentor_options, key=mentor_key)
        mentor_sel = mentor_lookup.get(mentor_sel_label)

    with fcol3:
        st.write("")
        if st.button("Refresh data", key=f"{key_prefix}_refresh"):
            st.cache_data.clear()
            st.rerun()

    filt_resp = response_df.copy()
    filt_q = question_df.copy()
    if not single_lock and centre_sel not in ("All Learning Centres", "All My Centres"):
        filt_resp = filt_resp[filt_resp["centre"] == centre_sel]
        filt_q = filt_q[filt_q["centre"] == centre_sel]
    if mentor_sel:
        filt_resp = filt_resp[filt_resp["mentor"] == mentor_sel]
        filt_q = filt_q[filt_q["mentor"] == mentor_sel]

    if allow_raw_download:
        render_raw_download_button(raw_df, filt_resp, key_prefix, form_name)

    overall = filt_resp["avg"].mean() if not filt_resp.empty else None
    cat1_avg = filt_resp["cat1"].mean() if not filt_resp.empty else None
    cat2_avg = filt_resp["cat2"].mean() if not filt_resp.empty else None
    kpi_row(overall, cat1_avg, cat2_avg, len(filt_resp), cat1_label, cat2_label)

    if mentor_locked:
        action_scopes = [("Course", "course")]
    elif single_lock:
        action_scopes = [("Mentor/Faculty", "mentor"), ("Course", "course")]
    else:
        action_scopes = None
    render_action_items(
        build_action_items(filt_resp, filt_q, cat1_label, cat2_label, scopes=action_scopes),
        cat1_label, cat2_label,
    )

    section_header("📈", "Average score over time")
    with st.container(border=True):
        trend_chart(trend_by_date(filt_resp))

    if not mentor_locked:
        # Mentor-locked logins have nothing left to break down once Course
        # is removed (a single mentor, single centre) — skip the section
        # entirely rather than show an empty header.
        section_header("🧭", "Breakdowns")
        if single_lock:
            with st.container(border=True):
                st.markdown("**By Mentor / Faculty**")
                grouped_breakdown_chart(group_avg(filt_resp, "mentor"), cat1_label, cat2_label)
        else:
            b1, b2 = st.columns(2)
            with b1:
                with st.container(border=True):
                    st.markdown("**By Learning Centre**")
                    grouped_breakdown_chart(group_avg(filt_resp, "centre"), cat1_label, cat2_label)
            with b2:
                with st.container(border=True):
                    st.markdown("**By Mentor / Faculty**")
                    grouped_breakdown_chart(group_avg(filt_resp, "mentor"), cat1_label, cat2_label)

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

    if mentor_locked:
        comment_extra_cols = []  # only one mentor, one centre in scope — both columns would be constant
    elif single_lock:
        comment_extra_cols = [("Mentor", "mentor")]
    else:
        comment_extra_cols = [("Centre", "centre"), ("Mentor", "mentor")]
    render_comments_panel(filt_resp, key_prefix, extra_cols=comment_extra_cols)

    st.caption(f"Data refreshes automatically every {CACHE_TTL_SECONDS // 60} minutes, or click \"Refresh data\" above for an immediate pull.")


def render_infrastructure_dashboard(key_prefix, endpoint_key, form_name, cat1_label, cat2_label, qmap,
                                     centre_col, course_col, batch_col, comment_cols=None, special_cols=None,
                                     centre_lock=None, allow_raw_download=False):
    """Sibling to render_dashboard() for the Centre Infrastructure Feedback
    Form. Kept as its own function rather than bolted onto render_dashboard()
    because the shape is genuinely different: no Mentor/Faculty dimension at
    all, only 2 Likert questions total, and most of the signal is Yes/No /
    Yes/No/Not Sure questions. Concretely, versus render_dashboard() this
    drops the Mentor filter, the "By Mentor" breakdown, the question-ranking
    section (redundant when a category is a single question), and the
    mentor x question heatmap — see dashboard-build-notes for the reasoning.
    """
    try:
        raw_df, fetched_at = load_sheet(endpoint_key)
    except KeyError:
        st.info(
            f"The {form_name} data endpoint isn't set up in secrets.toml yet "
            f"(missing `sheet_endpoints.{endpoint_key}`). Add it once you've "
            f"published that sheet to the web — see SETUP_GUIDE.md."
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
        raw_df, centre_col, None, course_col, batch_col, qmap,
        comment_cols=comment_cols, special_cols=special_cols
    )

    if response_df.empty:
        st.info(f"No responses yet in the {form_name} sheet. This dashboard will populate automatically once students start submitting the form.")
        return

    single_lock = bool(centre_lock) and len(centre_lock) == 1
    if centre_lock:
        locked_display_names = [CENTRE_DISPLAY_NAMES[c] for c in centre_lock]
        response_df = response_df[response_df["centre"].isin(locked_display_names)]
        question_df = question_df[question_df["centre"].isin(locked_display_names)]
        if response_df.empty:
            st.info(f"No responses yet for {', '.join(locked_display_names)} in the {form_name} sheet.")
            return

    # Filters — Centre + Course only; there's no Mentor/Faculty dimension on this form.
    fcol1, fcol2, fcol3 = st.columns([2, 2, 1])
    if single_lock:
        centre_sel = locked_display_names[0]
        with fcol1:
            st.markdown("**Learning Centre**")
            st.markdown(f"🔒 {centre_sel}")
    elif centre_lock:
        with fcol1:
            centre_options = ["All My Centres"] + locked_display_names
            centre_sel = render_centre_filter(key_prefix, centre_options)
    else:
        centres = sorted(response_df["centre"].dropna().unique().tolist())
        with fcol1:
            centre_options = ["All Learning Centres"] + centres
            centre_sel = render_centre_filter(key_prefix, centre_options)
    with fcol2:
        courses = sorted(response_df["course"].dropna().unique().tolist())
        course_sel = st.selectbox("Course", ["All Courses"] + courses, key=f"{key_prefix}_course")
    with fcol3:
        st.write("")
        if st.button("Refresh data", key=f"{key_prefix}_refresh"):
            st.cache_data.clear()
            st.rerun()

    filt_resp = response_df.copy()
    filt_q = question_df.copy()
    if not single_lock and centre_sel not in ("All Learning Centres", "All My Centres"):
        filt_resp = filt_resp[filt_resp["centre"] == centre_sel]
        filt_q = filt_q[filt_q["centre"] == centre_sel]
    if course_sel != "All Courses":
        filt_resp = filt_resp[filt_resp["course"] == course_sel]
        filt_q = filt_q[filt_q["course"] == course_sel]

    if allow_raw_download:
        render_raw_download_button(raw_df, filt_resp, key_prefix, form_name)

    overall = filt_resp["avg"].mean() if not filt_resp.empty else None
    cat1_avg = filt_resp["cat1"].mean() if not filt_resp.empty else None
    cat2_avg = filt_resp["cat2"].mean() if not filt_resp.empty else None
    kpi_row(overall, cat1_avg, cat2_avg, len(filt_resp), cat1_label, cat2_label)

    action_scopes = [("Course", "course")] if single_lock else [("Learning Centre", "centre"), ("Course", "course")]
    render_action_items(
        build_action_items(filt_resp, filt_q, cat1_label, cat2_label, scopes=action_scopes),
        cat1_label, cat2_label,
    )

    section_header("📈", "Average score over time")
    with st.container(border=True):
        trend_chart(trend_by_date(filt_resp))

    if not single_lock:
        # A centre-locked login has nothing left to break down once Course
        # is removed (a single centre) — skip the section entirely rather
        # than show an empty header.
        section_header("🧭", "Breakdowns")
        with st.container(border=True):
            st.markdown("**By Learning Centre**")
            grouped_breakdown_chart(group_avg(filt_resp, "centre"), cat1_label, cat2_label)

    section_header("📊", "Response spread per facility question")
    st.caption("An average can hide a split opinion — this shows the actual mix of ratings, not just the mean.")
    d1, d2 = st.columns(2)
    with d1:
        with st.container(border=True):
            st.markdown(f"**{cat1_label}**")
            distribution_chart(question_distribution(filt_q, "cat1"))
    with d2:
        with st.container(border=True):
            st.markdown(f"**{cat2_label}**")
            distribution_chart(question_distribution(filt_q, "cat2"))

    render_special_questions(filt_resp, special_cols, cols_per_row=3)

    infra_comment_cols = [("Course", "course")] if single_lock else [("Centre", "centre"), ("Course", "course")]
    render_comments_panel(filt_resp, key_prefix, extra_cols=infra_comment_cols)

    st.caption(f"Data refreshes automatically every {CACHE_TTL_SECONDS // 60} minutes, or click \"Refresh data\" above for an immediate pull.")


# ---------------------------------------------------------------------------
# Authentication & roles
# ---------------------------------------------------------------------------
#
# Two kinds of login: a centre login locked to one of the 6 CENTRE_KEYWORDS
# (a "centre:<Name>" role) and an admin login (an "admin" role, sees every
# centre). Plain email + bcrypt-hashed-password auth via
# streamlit-authenticator, with a signed cookie so a viewer stays signed in
# across page reloads — chosen over Google Sign-In so Anshuman doesn't need
# to stand up a Google Cloud OAuth project just for this. Credentials live in
# secrets.toml under [credentials]; see SETUP_GUIDE.md for the exact schema
# and how new accounts get their passwords hashed.

def build_authenticator():
    """Returns an Authenticate instance built from secrets, or None if
    [credentials] isn't configured there yet."""
    try:
        creds_secrets = st.secrets["credentials"]
    except KeyError:
        return None
    creds = dict(creds_secrets.to_dict())  # plain, mutable copy — st.secrets itself is read-only
    return stauth.Authenticate(
        credentials={"usernames": creds.get("usernames", {})},
        cookie_name=creds.get("cookie_name", "jetking_feedback_auth"),
        cookie_key=creds["cookie_key"],
        cookie_expiry_days=creds.get("cookie_expiry_days", 30),
        auto_hash=False,  # passwords in secrets.toml are already bcrypt-hashed — see SETUP_GUIDE.md
    )


def resolve_role(roles):
    """roles: the list from st.session_state['roles'] for the signed-in user
    (a per-user field in secrets.toml — see SETUP_GUIDE.md). Returns
    (is_admin, centre_lock, mentor_lock): centre_lock is a list of one or
    more CENTRE_KEYWORDS keys for a centre-restricted login, or None for an
    admin. A single-centre login is the normal case:
    roles = ["centre:Khar"]. A multi-centre login (sees only that specific
    subset, not every centre) lists more than one name comma-separated in
    one role: roles = ["centre:Laxminagar,Bhawaniopore"] — or, equivalently,
    as separate "centre:X" entries in the roles list; both forms are
    merged into one deduplicated list. If both come back False/None, the
    account has no recognized role configured — callers must treat that as
    no access, not as admin access, so a typo'd role fails closed rather
    than accidentally granting everything. An unrecognized centre name
    mixed in with valid ones is silently dropped rather than denying the
    whole role, so one typo only narrows access instead of removing it.

    mentor_lock: a mentor-name string (exactly as it appears in that
    sheet's mentor column, e.g. the Google Form's mentor dropdown), or
    None. Set via a "mentor:<Centre>:<Mentor Name>" role, e.g.
    roles = ["mentor:Vashi:Pradnya Shelar"] — this locks the login to
    exactly that one centre (added into centre_lock, same as a "centre:"
    role would) AND to exactly that one mentor's own data within it. This
    is a trainer-level login: they see only their own numbers, not the
    rest of their centre's mentors. Intended to be paired with exactly one
    centre; the mentor name must match the sheet's mentor column exactly
    (case/whitespace-insensitive) or that trainer's dashboard will show
    "no responses yet" even though data exists under a slightly different
    spelling — double-check against the live sheet before shipping a new
    mentor-locked account, not just the staff directory (names can drift
    between the two)."""
    roles = roles or []
    if "admin" in roles:
        return True, None, None
    locked = []
    mentor_lock = None
    for r in roles:
        if not isinstance(r, str):
            continue
        if r.startswith("centre:"):
            for key in r.split(":", 1)[1].split(","):
                key = key.strip()
                if key in CENTRE_DISPLAY_NAMES and key not in locked:
                    locked.append(key)
        elif r.startswith("mentor:"):
            # "mentor:<Centre>:<Mentor Name>" — split on ":" at most twice
            # so a mentor name containing ":" (unlikely, but not worth
            # crashing over) doesn't break the parse.
            parts = r.split(":", 2)
            if len(parts) == 3:
                _, centre_key, name = parts
                centre_key, name = centre_key.strip(), name.strip()
                if centre_key in CENTRE_DISPLAY_NAMES and name:
                    if centre_key not in locked:
                        locked.append(centre_key)
                    mentor_lock = name
    if locked:
        return False, locked, mentor_lock
    return False, None, None


def mentor_has_any_responses(endpoint_key, centre_col, mentor_col, course_col, batch_col, qmap,
                              centre_lock, mentor_lock):
    """Used only to decide, for a mentor-locked (trainer) login, whether a
    tab is even worth showing — e.g. a PD trainer who only ever appears in
    the Employability sheet shouldn't see an empty Technical tab. Applies
    the same centre_lock/mentor_lock filtering render_dashboard() would and
    reports whether anything survives. load_sheet() is st.cache_data-cached,
    so this doesn't cost an extra fetch once render_dashboard() also runs
    for a tab that stays visible.
    Fails OPEN (returns True) on a load error — a transient network hiccup
    should surface as render_dashboard()'s own retry/error UI inside a
    visible tab, not silently hide the tab as if there were no data."""
    try:
        raw_df, _ = load_sheet(endpoint_key)
    except Exception:
        return True
    response_df, _ = build_response_and_question_df(raw_df, centre_col, mentor_col, course_col, batch_col, qmap)
    if response_df.empty:
        return False
    if centre_lock:
        locked_display_names = [CENTRE_DISPLAY_NAMES[c] for c in centre_lock]
        response_df = response_df[response_df["centre"].isin(locked_display_names)]
    if mentor_lock:
        mkey = mentor_lock.strip().casefold()
        response_df = response_df[response_df["mentor"].str.strip().str.casefold() == mkey]
    return not response_df.empty


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

def main():
    st.markdown(
        """
        <div class="hero-banner">
            <h1>🎓 Jetking Feedback Dashboards</h1>
            <p>Live from the Technical, Employability, and Centre Infrastructure Feedback Google Sheets.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    authenticator = build_authenticator()
    if authenticator is None:
        st.error(
            "Sign-in isn't configured yet (missing `[credentials]` in secrets.toml). "
            "See SETUP_GUIDE.md."
        )
        st.stop()

    authenticator.login(location="main", fields={"Form name": "Sign in", "Username": "Email", "Login": "Sign in"})
    auth_status = st.session_state.get("authentication_status")

    if auth_status is False:
        st.error("That email or password isn't right.")
        st.stop()
    if auth_status is not True:
        st.info("Sign in above to view the dashboards.")
        st.stop()

    is_admin, centre_lock, mentor_lock = resolve_role(st.session_state.get("roles"))
    if not is_admin and not centre_lock:
        # Note: deliberately NOT calling authenticator.logout() here. That
        # triggers a cookie-clearing rerun that flashes this message for
        # under a second before resetting to a bare sign-in form, which
        # reads as a bug rather than a denial. Leaving the session cookie in
        # place is safe — this same role check re-runs on every page load,
        # so a no-role account still can't reach any dashboard content; it
        # just sees this message consistently until an admin fixes its role.
        st.error("Your account is signed in but has no access role configured — contact Anshuman.")
        authenticator.logout("Log out", location="main")
        st.stop()

    allow_raw_download = st.session_state.get("email") in RAW_DOWNLOAD_EMAILS

    top_l, top_r = st.columns([5, 1])
    with top_l:
        if centre_lock and mentor_lock:
            who = f"{', '.join(CENTRE_DISPLAY_NAMES[c] for c in centre_lock)} — {mentor_lock}"
        elif centre_lock:
            who = ", ".join(CENTRE_DISPLAY_NAMES[c] for c in centre_lock)
        else:
            who = "Admin — all centres"
        st.caption(f"Signed in as **{st.session_state.get('name')}** · {who}")
    with top_r:
        authenticator.logout("Log out", location="main")

    # A mentor-locked (trainer) login only sees tabs relevant to them: the
    # Infrastructure tab is centre-facility feedback, not about any one
    # mentor, so it's hidden entirely; Technical/Employability are each
    # hidden individually if that trainer has no data there at all (e.g. a
    # PD trainer who only ever appears in the Employability sheet). Every
    # other login (admin, centre-locked manager/supervisor) is unaffected —
    # all three tabs always show, exactly as before, including an empty one
    # with its own "No responses yet" message.
    if mentor_lock:
        show_tech = mentor_has_any_responses(
            endpoint_key="technical_url", centre_col="Jetking Learning Centre Name",
            mentor_col="Technical Mentor Name (Please select from the dropdown)",
            course_col="Course Name", batch_col="Batch Code (eg. 2627-JU-1234)", qmap=TECH_Q,
            centre_lock=centre_lock, mentor_lock=mentor_lock,
        )
        show_emp = mentor_has_any_responses(
            endpoint_key="employability_url", centre_col="Jetking Learning Centre Name",
            mentor_col="Mentor Name", course_col="Course Name", batch_col="Batch Code", qmap=EMP_Q,
            centre_lock=centre_lock, mentor_lock=mentor_lock,
        )
        show_infra = False
    else:
        show_tech = show_emp = show_infra = True

    if not (show_tech or show_emp or show_infra):
        st.info(
            "No feedback data found for you yet in either the Technical or Employability "
            "sheet — this will populate automatically once students submit feedback for "
            "one of your sessions."
        )
        return

    tab_titles = []
    if show_tech:
        tab_titles.append("Technical Session Feedback")
    if show_emp:
        tab_titles.append("Employability Session Feedback")
    if show_infra:
        tab_titles.append("Centre Infrastructure Feedback")
    tabs = iter(st.tabs(tab_titles))

    if show_tech:
        with next(tabs):
            render_dashboard(
                key_prefix="tech",
                endpoint_key="technical_url",
                form_name="Technical Session Feedback",
                cat1_label="Session Delivery Avg",
                cat2_label="Mentor Behavior Avg",
                qmap=TECH_Q,
                centre_col="Jetking Learning Centre Name",
                mentor_col="Technical Mentor Name (Please select from the dropdown)",
                course_col="Course Name",
                batch_col="Batch Code (eg. 2627-JU-1234)",
                comment_cols=TECH_COMMENT_COLS,
                special_cols=TECH_SPECIAL_Q,
                centre_lock=centre_lock,
                mentor_lock=mentor_lock,
                allow_raw_download=allow_raw_download,
            )

    if show_emp:
        with next(tabs):
            render_dashboard(
                key_prefix="emp",
                endpoint_key="employability_url",
                form_name="Employability Session Feedback",
                cat1_label="Session Content Avg",
                cat2_label="Mentor Behavior Avg",
                qmap=EMP_Q,
                centre_col="Jetking Learning Centre Name",
                mentor_col="Mentor Name",
                course_col="Course Name",
                batch_col="Batch Code",
                comment_cols=EMP_COMMENT_COLS,
                special_cols=EMP_SPECIAL_Q,
                centre_lock=centre_lock,
                mentor_lock=mentor_lock,
                allow_raw_download=allow_raw_download,
            )

    if show_infra:
        with next(tabs):
            render_infrastructure_dashboard(
                key_prefix="infra",
                endpoint_key="infrastructure_url",
                form_name="Centre Infrastructure Feedback",
                cat1_label="Drinking Water Avg",
                cat2_label="Washroom Facility Avg",
                qmap=INFRA_Q,
                centre_col="Jetking Learning Centre Name",
                course_col="Course Name",
                batch_col="Batch Code (eg. 2627-JU-1234)",
                comment_cols=INFRA_COMMENT_COLS,
                special_cols=INFRA_SPECIAL_Q,
                centre_lock=centre_lock,
                allow_raw_download=allow_raw_download,
            )


if __name__ == "__main__":
    main()
