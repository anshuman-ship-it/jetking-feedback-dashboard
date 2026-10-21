"""
Telegram bot registrations — ADMIN-ONLY dashboard tab.

Reads the "Registrations" tab of the "Telegram Bot – Student Registrations"
Google Sheet (written by the Apps Script bot, Code-8.gs onwards) and shows:
  * headline numbers (total, last 7 days, centre known, finished current round)
  * the registration trend over time (daily or weekly, with cumulative total)
  * registrations per centre, plus a weekly-by-centre breakdown

Data access (configure ONE in secrets.toml / Streamlit Cloud Secrets):

    [telegram_registrations]
    sheet_id = "1ZY9mgJEyvmM5P5h50-gxvloX95Cr5vW93DveVD3NGPk"   # preferred
    # csv_url = "https://docs.google.com/spreadsheets/d/e/.../pub?gid=0&single=true&output=csv"
    # exclude_chat_ids = [8895424716]   # optional: hide test/staff accounts

`sheet_id` reads the sheet privately through the existing [gcp_service_account]
(share the sheet with that service account's email as Viewer). `csv_url` is the
"Publish to web" fallback — note that link is readable by anyone who has it, and
this sheet holds student names/usernames, so prefer `sheet_id`.

Access control: render_telegram_registrations() refuses to render anything
unless is_admin is True — main() should also only add the tab for admins.
"""

from __future__ import annotations

import io
import time

import altair as alt
import pandas as pd
import requests
import streamlit as st

# Must match CENTRES[].key in the Apps Script (Code-8.gs) and the dashboard's
# canonical centre keys.
CENTRE_KEYS = ["Dadar", "Vashi", "Laxminagar", "Maninagar", "Bhawaniopore", "Khar"]
UNKNOWN_CENTRE = "Not answered yet"
REG_TAB_NAME = "Registrations"


# ---------------------------------------------------------------- loading ----

def _config() -> dict:
    try:
        return dict(st.secrets.get("telegram_registrations", {}))
    except Exception:
        return {}


def _load_via_service_account(sheet_id: str) -> pd.DataFrame:
    import gspread  # already used for the password store
    from google.oauth2.service_account import Credentials

    creds = Credentials.from_service_account_info(
        dict(st.secrets["gcp_service_account"]),
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
    )
    ws = gspread.authorize(creds).open_by_key(sheet_id).worksheet(REG_TAB_NAME)
    values = ws.get_all_values()
    if not values:
        return pd.DataFrame()
    return pd.DataFrame(values[1:], columns=values[0])


def _load_via_csv(url: str) -> pd.DataFrame:
    last_err = None
    for attempt in range(3):
        try:
            resp = requests.get(url, timeout=45)
            resp.raise_for_status()
            return pd.read_csv(io.StringIO(resp.text), dtype=str)
        except Exception as err:  # noqa: BLE001
            last_err = err
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Couldn't fetch the registrations CSV: {last_err}")


@st.cache_data(ttl=300, show_spinner="Loading Telegram registrations…")
def load_registrations(sheet_id: str | None, csv_url: str | None) -> pd.DataFrame:
    if sheet_id:
        raw = _load_via_service_account(sheet_id)
    elif csv_url:
        raw = _load_via_csv(csv_url)
    else:
        raise RuntimeError("not_configured")
    return prepare_registrations(raw)


def prepare_registrations(raw: pd.DataFrame) -> pd.DataFrame:
    """Normalise the raw sheet into one clean row per registered student."""
    df = raw.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]
    if "chat_id" not in df.columns or "registered_at" not in df.columns:
        raise RuntimeError(
            "Registrations sheet is missing chat_id / registered_at columns — "
            f"found: {list(df.columns)}"
        )
    for col in ("centre", "round", "round_completed_at", "first_name", "username"):
        if col not in df.columns:
            df[col] = ""

    df["chat_id"] = df["chat_id"].astype(str).str.strip()
    df = df[df["chat_id"].ne("") & df["chat_id"].ne("nan")]
    df = df.drop_duplicates("chat_id", keep="first")

    df["registered_at"] = pd.to_datetime(df["registered_at"], errors="coerce")
    df = df[df["registered_at"].notna()]

    centre = df["centre"].fillna("").astype(str).str.strip()
    known = {k.lower(): k for k in CENTRE_KEYS}
    df["centre"] = centre.map(
        lambda c: known.get(c.lower(), f"Unrecognized ({c})" if c else UNKNOWN_CENTRE)
    )
    df["finished_current_round"] = (
        df["round_completed_at"].fillna("").astype(str).str.strip().ne("")
    )
    return df.sort_values("registered_at").reset_index(drop=True)


# ------------------------------------------------------------ aggregation ----

def trend_table(df: pd.DataFrame, freq: str) -> pd.DataFrame:
    """New registrations per period (including empty periods) + running total."""
    if df.empty:
        return pd.DataFrame(columns=["period", "new", "cumulative", "label"])
    if freq == "W":
        starts = df["registered_at"].dt.to_period("W-SUN").dt.start_time
        idx = pd.date_range(starts.min(), pd.Timestamp.now().normalize(), freq="7D")
    else:
        starts = df["registered_at"].dt.normalize()
        idx = pd.date_range(starts.min(), pd.Timestamp.now().normalize(), freq="D")
    counts = starts.value_counts().reindex(idx, fill_value=0).sort_index()
    out = pd.DataFrame({"period": counts.index, "new": counts.values})
    out["cumulative"] = out["new"].cumsum()
    if freq == "W":
        out["label"] = out["period"].dt.strftime("w/c %d %b")
    else:
        out["label"] = out["period"].dt.strftime("%d %b")
    return out


def centre_table(df: pd.DataFrame) -> pd.DataFrame:
    cutoff = pd.Timestamp.now() - pd.Timedelta(days=7)
    order = CENTRE_KEYS + sorted(
        c for c in df["centre"].unique() if c not in CENTRE_KEYS and c != UNKNOWN_CENTRE
    ) + [UNKNOWN_CENTRE]
    rows = []
    for c in order:
        sub = df[df["centre"] == c]
        if c not in CENTRE_KEYS and sub.empty:
            continue
        rows.append({
            "Centre": c,
            "Registered": len(sub),
            "Last 7 days": int((sub["registered_at"] >= cutoff).sum()),
            "Finished current round": int(sub["finished_current_round"].sum()),
            "Latest registration": sub["registered_at"].max() if len(sub) else pd.NaT,
        })
    return pd.DataFrame(rows)


# --------------------------------------------------------------- rendering ----

def render_telegram_registrations(is_admin: bool) -> None:
    if not is_admin:  # defence in depth — main() should not add the tab at all
        st.error("This section is available to admins only.")
        return

    st.subheader("📲 Telegram bot registrations")
    cfg = _config()
    try:
        df = load_registrations(cfg.get("sheet_id"), cfg.get("csv_url"))
    except RuntimeError as err:
        if str(err) == "not_configured":
            st.info(
                "Telegram registrations aren't connected yet. Add a "
                "`[telegram_registrations]` block with `sheet_id` (or `csv_url`) "
                "to the app's Secrets."
            )
        else:
            st.error(str(err))
        return
    except Exception as err:  # noqa: BLE001
        st.error(f"Couldn't load the Telegram registrations sheet: {err}")
        return

    exclude = {str(x).strip() for x in cfg.get("exclude_chat_ids", [])}
    if exclude:
        df = df[~df["chat_id"].isin(exclude)]

    if df.empty:
        st.info("No registrations yet.")
        return

    # ---- filters
    f1, f2, f3 = st.columns([2, 2, 1])
    centre_options = ["All centres"] + [c for c in centre_table(df)["Centre"]]
    centre_sel = f1.selectbox("Centre", centre_options, key="tg_centre")
    gran = f2.radio("View by", ["Week", "Day"], horizontal=True, key="tg_gran")
    if f3.button("🔄 Refresh", key="tg_refresh"):
        load_registrations.clear()
        st.rerun()

    view = df if centre_sel == "All centres" else df[df["centre"] == centre_sel]

    # ---- headline numbers (for the current centre selection)
    last7 = int((view["registered_at"] >= pd.Timestamp.now() - pd.Timedelta(days=7)).sum())
    known = int((df["centre"] != UNKNOWN_CENTRE).sum())
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Registered", len(view))
    m2.metric("New in last 7 days", last7)
    m3.metric("Finished current round", int(view["finished_current_round"].sum()))
    m4.metric("Centre known (all)", f"{known} / {len(df)}",
              help="Students who have tapped their centre in the bot. The rest show as "
                   f"“{UNKNOWN_CENTRE}”.")

    # ---- trend
    st.markdown("##### Registration trend")
    trend = trend_table(view, "W" if gran == "Week" else "D")
    if trend.empty:
        st.info("No registrations for this selection.")
    else:
        x = alt.X("label:N", sort=None, title=None, axis=alt.Axis(labelAngle=-40))
        bars = alt.Chart(trend).mark_bar(opacity=0.75).encode(
            x=x,
            y=alt.Y("new:Q", title=f"New per {gran.lower()}"),
            tooltip=[alt.Tooltip("label:N", title=gran), alt.Tooltip("new:Q", title="New"),
                     alt.Tooltip("cumulative:Q", title="Total so far")],
        )
        line = alt.Chart(trend).mark_line(point=True, strokeWidth=2.5, color="#E4572E").encode(
            x=x,
            y=alt.Y("cumulative:Q", title="Total registered"),
            tooltip=[alt.Tooltip("label:N", title=gran), alt.Tooltip("cumulative:Q", title="Total so far")],
        )
        st.altair_chart(
            alt.layer(bars, line).resolve_scale(y="independent").properties(height=320),
            use_container_width=True,
        )
        st.caption("Bars: new registrations per period · Line: running total.")

    # ---- by centre (always all centres, so the comparison stays visible)
    st.markdown("##### By centre")
    ct = centre_table(df)
    c1, c2 = st.columns([3, 2])
    c1.altair_chart(
        alt.Chart(ct).mark_bar().encode(
            y=alt.Y("Centre:N", sort=list(ct["Centre"]), title=None),
            x=alt.X("Registered:Q", title="Students registered"),
            color=alt.condition(
                alt.datum.Centre == UNKNOWN_CENTRE, alt.value("#9AA0A6"), alt.value("#2E86AB")
            ),
            tooltip=["Centre", "Registered", "Last 7 days", "Finished current round"],
        ).properties(height=40 * len(ct) + 20),
        use_container_width=True,
    )
    c2.dataframe(
        ct.assign(**{"Latest registration": ct["Latest registration"].dt.strftime("%d %b %Y")})
          .fillna({"Latest registration": "—"}),
        hide_index=True, use_container_width=True,
    )

    # ---- weekly by centre
    with st.expander("Weekly new registrations by centre"):
        wk = view.assign(week=view["registered_at"].dt.to_period("W-SUN").dt.start_time)
        wk = wk.groupby(["week", "centre"]).size().reset_index(name="new")
        wk["label"] = wk["week"].dt.strftime("w/c %d %b")
        st.altair_chart(
            alt.Chart(wk).mark_bar().encode(
                x=alt.X("label:N", sort=alt.SortField("week"), title=None,
                        axis=alt.Axis(labelAngle=-40)),
                y=alt.Y("new:Q", title="New registrations"),
                color=alt.Color("centre:N", title="Centre"),
                tooltip=[alt.Tooltip("label:N", title="Week"), alt.Tooltip("centre:N", title="Centre"),
                         alt.Tooltip("new:Q", title="New")],
            ).properties(height=300),
            use_container_width=True,
        )

    st.caption("Source: “Telegram Bot – Student Registrations” sheet · refreshes every 5 minutes "
               "(or use Refresh). Students who registered before centre capture was added appear "
               f"as “{UNKNOWN_CENTRE}” until they tap their centre in the bot.")