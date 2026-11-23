"""
Academic Excellence (AE) audit dashboard — Jetking

The AE team observes classes and rates trainers on two Google Forms
(Technical, Employability/PD). Each form's responses tab is published to the
web as CSV, and its link lives in secrets.toml under [sheet_endpoints] as
`ae_technical_url` / `ae_employability_url`.

Called from app.py as render_academic_excellence(globals()). Every chart and
helper is reused from app.py (passed in as `h`), so this tab looks and
behaves like the student feedback tabs. Kept in its own file, like
telegram_registrations.py, so app.py itself only needed a few small edits.

Visible to admins (as an extra tab) and to accounts with the "ae" role
(this tab only, no student feedback tabs). See apply_academic_excellence.py.

How the AE forms differ from the student forms:
- The trainer dropdown is split into one column per centre
  ("Technical Mentor Name – Vashi Learning Centre", ...). Only one is filled
  per response; they are merged into a single trainer column here. When the
  auditor picked "Other – trainer not in this list", the typed name from
  "New trainer's full name" is used instead.
- "Your Full Name" is the auditor, which gets its own filter.
- Dates come from "Observation Date & Time" (when the class happened), not
  the submission timestamp.
- The 9 Behavioural questions use the same codes (B1–B9) and short labels as
  the student forms, which is what powers the Auditor vs Students section.
"""

from types import SimpleNamespace

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

OTHER_PREFIX = "other"  # "Other – trainer not in this list"

BEHAVIOUR_Q = [
    ("B1", "Mentor treated all students with respect and professionalism.", "Respect & professionalism", "cat2"),
    ("B2", "Mentor encouraged student participation and interaction during the session.", "Encourages participation", "cat2"),
    ("B3", "Mentor is approachable and willing to clarify doubts.", "Approachable, clarifies doubts", "cat2"),
    ("B4", "Mentor created a positive and motivating learning environment.", "Positive, motivating environment", "cat2"),
    ("B5", "Mentor provided constructive feedback on assignments, presentations, and practical work.", "Constructive feedback on work", "cat2"),
    ("B6", "Mentor demonstrated patience while explaining difficult concepts.", "Patient explaining concepts", "cat2"),
    ("B7", "Mentor maintained discipline while ensuring a friendly classroom atmosphere.", "Discipline & friendly atmosphere", "cat2"),
    ("B8", "Mentor encouraged teamwork, collaboration, and peer learning.", "Encourages teamwork", "cat2"),
    ("B9", "Overall, you are satisfied with your mentor's teaching and mentoring approach.", "Overall mentor satisfaction", "cat2"),
]

AE_TECH_Q = [
    ("T1", "Mentor started class on time?", "Started class on time", "cat1"),
    ("T2", "Mentor conducted the SLP activities like student presentation / mindmap / group discussion / yoga / think-pair-share ?", "SLP activities conducted", "cat1"),
    ("T3", "Mentor provided Presee's/Pre-reads materials for students to prepare before coming to class?", "Pre-read materials provided", "cat1"),
    ("T4", "Mentor displayed the content on the TV monitor in the class room uploaded to the Learnyst LMS? (Kingdemy)", "LMS content shown on TV", "cat1"),
    ("T5", "Mentor exhibited technical expertise in the topic being taught?", "Technical expertise", "cat1"),
    ("T6", "Mentor asked questions at regular intervals?", "Asks questions regularly", "cat1"),
] + BEHAVIOUR_Q

AE_EMP_Q = [
    ("P1", "Mentor started class on time?", "Started class on time", "cat1"),
    ("P2", "Employability class conducted according to the logsheet?", "Class per logsheet", "cat1"),
    ("P3", "Students were able to apply concepts taught to them confidently?", "Students apply concepts confidently", "cat1"),
    ("P4", "The session was engaging and interactive.", "Engaging & interactive", "cat1"),
    ("P5", "The activities helped the students participate actively", "Activities drove participation", "cat1"),
    ("P6", "Students were expressing thoughts and opinions.", "Students expressed opinions", "cat1"),
] + BEHAVIOUR_Q

AE_FORMS = {
    "tech": dict(
        endpoint_key="ae_technical_url",
        form_name="AE Technical Observation",
        mentor_prefix="technical mentor name",
        qmap=AE_TECH_Q,
        cat1_label="Technical Parameters Avg",
        student_endpoint="technical_url",
        student_q="TECH_Q",
        student_cols="TECH_COLS",
        student_form="Technical Session Feedback",
    ),
    "emp": dict(
        endpoint_key="ae_employability_url",
        form_name="AE Employability (PD) Observation",
        mentor_prefix="employability mentor name",
        qmap=AE_EMP_Q,
        cat1_label="Session Parameters Avg",
        student_endpoint="employability_url",
        student_q="EMP_Q",
        student_cols="EMP_COLS",
        student_form="Employability Session Feedback",
    ),
}

CAT2_LABEL = "Behavioural Parameters Avg"

CENTRE_COL = "Jetking Learning Centre Name"
AUDITOR_COL = "Your Full Name"
COURSE_COL = "Course Name"
BATCH_COL = "Batch Code"
OBS_DATE_COL = "Observation Date & Time"
ATTENDANCE_COL = "Total attendance in class"
TOPIC_COL = "Activity / Session Topic"
COMMENT_COL = "Observations / Comments for improvements (if any):"

MERGED_TRAINER_COL = "_trainer"
MERGED_AUDITOR_COL = "_auditor"

ALL_CENTRES = "All Learning Centres"
ALL_TRAINERS = "All Trainers"
ALL_AUDITORS = "All Auditors"

GAP_FLAG = 1.0  # audit vs student difference (out of 5) worth calling out


def _norm(s):
    return " ".join(str(s).split()).strip().lower()


def _clean(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    s = str(v).strip()
    return "" if s.lower() == "nan" else s


def _find_col(df, *keywords):
    for c in df.columns:
        if all(k in _norm(c) for k in keywords):
            return c
    return None


def prepare_ae_raw(raw_df, mentor_prefix):
    """Adds two helper columns to a copy of the AE response sheet:
    _trainer (the one filled per-centre trainer column, or the typed new
    trainer name when "Other" was picked) and _auditor."""
    df = raw_df.copy()
    trainer_cols = [c for c in df.columns if _norm(c).startswith(mentor_prefix)]
    new_col = _find_col(df, "new trainer")
    auditor_col = AUDITOR_COL if AUDITOR_COL in df.columns else _find_col(df, "your full name")

    def merge(row):
        picked = ""
        for c in trainer_cols:
            v = _clean(row.get(c))
            if v:
                picked = v
                break
        if not picked or _norm(picked).startswith(OTHER_PREFIX):
            typed = " ".join(_clean(row.get(new_col)).split()) if new_col else ""
            return typed or picked
        return picked

    df[MERGED_TRAINER_COL] = df.apply(merge, axis=1) if len(df) else []
    df[MERGED_AUDITOR_COL] = df[auditor_col].map(_clean) if auditor_col else ""
    return df


def build_ae(h, df, qmap):
    """app.py's build_response_and_question_df, pointed at the merged trainer
    column and the observation date. Returns (response_df, question_df)."""
    ts_col = OBS_DATE_COL if OBS_DATE_COL in df.columns else "Timestamp"
    comment_cols = [(COMMENT_COL, "Observations")] if COMMENT_COL in df.columns else []
    return h.build_response_and_question_df(
        df, CENTRE_COL, MERGED_TRAINER_COL, COURSE_COL, BATCH_COL, qmap,
        ts_col=ts_col, comment_cols=comment_cols,
    )


def compare_with_students(ae_q, ae_resp, st_resp, st_q):
    """Auditor vs Students on the 9 shared Behavioural questions.
    Only trainers present in the (filtered) audit data are compared, so both
    sides cover the same people. Returns (per_trainer_df, per_question_df);
    either may be empty."""
    if ae_resp.empty or st_resp.empty:
        return pd.DataFrame(), pd.DataFrame()
    key = lambda s: s.astype(str).str.strip().str.casefold()

    ae = ae_resp.assign(_k=key(ae_resp["mentor"]))
    stu = st_resp.assign(_k=key(st_resp["mentor"]))
    trainers = set(ae["_k"]) - {""}
    stu = stu[stu["_k"].isin(trainers)]
    if stu.empty:
        return pd.DataFrame(), pd.DataFrame()

    a = ae.groupby("_k").agg(
        Trainer=("mentor", "first"), Centre=("centre", lambda s: ", ".join(sorted(set(s)))),
        Audits=("cat2", "count"), audit=("cat2", "mean"),
    )
    s = stu.groupby("_k").agg(Responses=("cat2", "count"), students=("cat2", "mean"))
    per_trainer = a.join(s, how="inner").reset_index(drop=True)
    per_trainer["Gap"] = per_trainer["audit"] - per_trainer["students"]
    per_trainer = per_trainer.sort_values("Gap", key=lambda g: g.abs(), ascending=False)

    aq = ae_q[(ae_q["cat"] == "cat2") & key(ae_q["mentor"]).isin(trainers)]
    sq = st_q[(st_q["cat"] == "cat2") & key(st_q["mentor"]).isin(trainers)]
    per_q = pd.DataFrame({
        "audit": aq.groupby("code")["score"].mean(),
        "students": sq.groupby("code")["score"].mean(),
    })
    order = [code for code, _, _, _ in BEHAVIOUR_Q]
    labels = {code: lbl for code, _, lbl, _ in BEHAVIOUR_Q}
    per_q = per_q.reindex([c for c in order if c in per_q.index])
    per_q["label"] = [labels[c] for c in per_q.index]
    return per_trainer, per_q.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def render_academic_excellence(app_globals):
    """Entry point, called from app.py as render_academic_excellence(globals())."""
    h = SimpleNamespace(**app_globals)
    st.caption(
        "Class observations by the Academic Excellence team. "
        "Ratings are 1 (Strongly Disagree) to 5 (Strongly Agree)."
    )
    t_tab, e_tab = st.tabs(["Technical observations", "Employability (PD) observations"])
    with t_tab:
        _render_form(h, "aet", AE_FORMS["tech"])
    with e_tab:
        _render_form(h, "aee", AE_FORMS["emp"])


def _render_form(h, key_prefix, cfg):
    try:
        raw_df, fetched_at = h.load_sheet(cfg["endpoint_key"])
    except KeyError:
        st.info(
            f"The {cfg['form_name']} link isn't set up yet — add "
            f"`{cfg['endpoint_key']}` under [sheet_endpoints] in secrets.toml "
            "(locally and in Streamlit Cloud's Secrets)."
        )
        return
    except Exception as e:
        st.error(f"Couldn't load {cfg['form_name']} data: {e}")
        if st.button("Try again", key=f"{key_prefix}_retry"):
            st.cache_data.clear()
            st.rerun()
        return
    st.caption(f"📅 Data as of {fetched_at.strftime('%b %d, %Y, %I:%M %p')}")

    qmap = cfg["qmap"]
    cat1_label = cfg["cat1_label"]
    df = prepare_ae_raw(raw_df, cfg["mentor_prefix"])
    all_resp, _ = build_ae(h, df, qmap)
    if all_resp.empty:
        st.info(
            f"No observations yet in the {cfg['form_name']} form. "
            "This will fill in automatically once the AE team starts submitting."
        )
        return
    all_resp["auditor"] = df.loc[all_resp["_raw_index"], MERGED_AUDITOR_COL].values

    # --- Filters: Centre → Trainer (scoped to centre) → Auditor ---
    f1, f2, f3, f4 = st.columns([2, 2, 2, 1])
    with f1:
        centres = sorted(c for c in all_resp["centre"].dropna().unique() if c)
        centre_sel = st.selectbox("Learning Centre", [ALL_CENTRES] + centres, key=f"{key_prefix}_centre")
    scoped = all_resp if centre_sel == ALL_CENTRES else all_resp[all_resp["centre"] == centre_sel]
    with f2:
        trainers = sorted(t for t in scoped["mentor"].dropna().unique() if t)
        t_key = f"{key_prefix}_trainer"
        if st.session_state.get(t_key) not in [ALL_TRAINERS] + trainers:
            st.session_state[t_key] = ALL_TRAINERS
        trainer_sel = st.selectbox("Trainer", [ALL_TRAINERS] + trainers, key=t_key)
    with f3:
        auditors = sorted(a for a in all_resp["auditor"].dropna().unique() if a)
        auditor_sel = st.selectbox("Auditor", [ALL_AUDITORS] + auditors, key=f"{key_prefix}_auditor")
    with f4:
        st.write("")
        if st.button("Refresh data", key=f"{key_prefix}_refresh"):
            st.cache_data.clear()
            st.rerun()

    sel = scoped
    if trainer_sel != ALL_TRAINERS:
        sel = sel[sel["mentor"] == trainer_sel]
    if auditor_sel != ALL_AUDITORS:
        sel = sel[sel["auditor"] == auditor_sel]
    if sel.empty:
        st.info("No observations match these filters.")
        return

    # Rebuild from just the selected raw rows so the per-question data
    # (which has no auditor column) is filtered exactly like the responses.
    filt_resp, filt_q = build_ae(h, df.loc[sel["_raw_index"]], qmap)
    filt_resp["auditor"] = df.loc[filt_resp["_raw_index"], MERGED_AUDITOR_COL].values

    # --- KPIs ---
    overall = filt_resp["avg"].mean()
    h.kpi_row(overall, filt_resp["cat1"].mean(), filt_resp["cat2"].mean(),
              len(filt_resp), cat1_label, CAT2_LABEL)
    att = pd.to_numeric(df.loc[filt_resp["_raw_index"]].get(ATTENDANCE_COL), errors="coerce") \
        if ATTENDANCE_COL in df.columns else pd.Series(dtype=float)
    k1, k2, k3, k4 = st.columns(4)
    with k1:
        h.kpi_card("Centres audited", filt_resp["centre"].nunique(), None)
    with k2:
        h.kpi_card("Trainers audited", filt_resp["mentor"].replace("", pd.NA).nunique(), None)
    with k3:
        h.kpi_card("Auditors active", filt_resp["auditor"].replace("", pd.NA).nunique(), None)
    with k4:
        h.kpi_card("Avg class attendance", f"{att.mean():.0f}" if att.notna().any() else "—", None)

    # --- Needs attention ---
    scopes = [("Learning Centre", "centre"), ("Trainer", "mentor"), ("Course", "course")]
    if centre_sel != ALL_CENTRES:
        scopes = [s for s in scopes if s[1] != "centre"]
    if trainer_sel != ALL_TRAINERS:
        scopes = [s for s in scopes if s[1] != "mentor"]
    h.render_action_items(
        h.build_action_items(filt_resp, filt_q, cat1_label, CAT2_LABEL, scopes=scopes),
        cat1_label, CAT2_LABEL,
    )

    # --- Auditor vs Students ---
    _render_comparison(h, key_prefix, cfg, filt_resp, filt_q, centre_sel)

    # --- Trend ---
    h.section_header("📈", "Average score over time (by observation date)")
    with st.container(border=True):
        h.trend_chart(h.trend_by_date(filt_resp))

    # --- Breakdowns ---
    show_centre = centre_sel == ALL_CENTRES
    show_trainer = trainer_sel == ALL_TRAINERS
    if show_centre or show_trainer:
        h.section_header("🧭", "Breakdowns")
        parts = []
        if show_centre:
            parts.append(("By Learning Centre", "centre"))
        if show_trainer:
            parts.append(("By Trainer", "mentor"))
        for col, (title, key) in zip(st.columns(len(parts)), parts):
            with col:
                with st.container(border=True):
                    st.markdown(f"**{title}**")
                    h.grouped_breakdown_chart(h.group_avg(filt_resp, key), cat1_label, CAT2_LABEL)

    # --- Questions ---
    q_order = st.radio("Order questions by", ["Form order", "Weakest first"],
                       horizontal=True, key=f"{key_prefix}_question_order")
    by_form = q_order == "Form order"
    h.section_header("🎯", "Question breakdown" + (", in form order" if by_form else ", weakest to strongest"))
    q1, q2 = st.columns(2)
    for col, cat, lbl, color in [(q1, "cat1", cat1_label, h.PALETTE["series1"]),
                                 (q2, "cat2", CAT2_LABEL, h.PALETTE["series2"])]:
        with col:
            with st.container(border=True):
                st.markdown(f"**{lbl.replace(' Avg', '')}**")
                h.question_chart(h.question_ranking(filt_q, cat, qmap, by_form), color, lbl)

    h.section_header("📊", "Rating spread per question")
    d1, d2 = st.columns(2)
    for col, cat, lbl in [(d1, "cat1", cat1_label), (d2, "cat2", CAT2_LABEL)]:
        with col:
            with st.container(border=True):
                st.markdown(f"**{lbl.replace(' Avg', '')}**")
                h.distribution_chart(h.question_distribution(filt_q, cat, qmap, by_form), top_down=by_form)

    if show_trainer:
        h.section_header("🔥", "Trainer × question")
        st.caption("Where a trainer's overall average hides one specific weak spot. Hover a cell for the question.")
        m1, m2 = st.columns(2)
        for col, cat, lbl in [(m1, "cat1", cat1_label), (m2, "cat2", CAT2_LABEL)]:
            with col:
                with st.container(border=True):
                    st.markdown(f"**{lbl.replace(' Avg', '')}**")
                    matrix, code_text = h.mentor_question_matrix(filt_q, cat, qmap)
                    h.heatmap_chart(matrix, code_text)

    _render_observations(h, key_prefix, df, filt_resp)

    st.caption(
        f"Data refreshes automatically every {h.CACHE_TTL_SECONDS // 60} minutes, "
        "or click \"Refresh data\" above for an immediate pull."
    )


def _render_comparison(h, key_prefix, cfg, ae_resp, ae_q, centre_sel):
    h.section_header("⚖️", "Auditor vs Students — Behavioural questions")
    st.caption(
        "The 9 Behavioural questions are the same on the audit form and the student "
        f"{cfg['student_form']} form. This compares the auditor's rating with what students "
        "said about the same trainers (all student responses to date)."
    )
    try:
        st_raw, _ = h.load_sheet(cfg["student_endpoint"])
        st_resp, st_q = h.build_response_and_question_df(
            st_raw, qmap=getattr(h, cfg["student_q"]), **getattr(h, cfg["student_cols"])
        )
    except Exception:
        st.info("Student feedback data couldn't be loaded right now, so the comparison is skipped.")
        return
    if centre_sel != ALL_CENTRES and not st_resp.empty:
        st_resp = st_resp[st_resp["centre"] == centre_sel]
        st_q = st_q[st_q["centre"] == centre_sel]

    per_trainer, per_q = compare_with_students(ae_q, ae_resp, st_resp, st_q)
    if per_trainer.empty:
        st.info(
            "None of the trainers in this selection have student feedback yet "
            "(or their names are spelled differently on the two forms)."
        )
        return

    c1, c2 = st.columns(2)
    with c1:
        with st.container(border=True):
            st.markdown("**By question**")
            fig = go.Figure()
            fig.add_bar(y=per_q["label"], x=per_q["audit"], name="Auditor", orientation="h",
                        marker_color=h.PALETTE["series1"],
                        hovertemplate="%{y}<br>Auditor: <b>%{x:.2f}</b> / 5<extra></extra>")
            fig.add_bar(y=per_q["label"], x=per_q["students"], name="Students", orientation="h",
                        marker_color=h.PALETTE["series3"],
                        hovertemplate="%{y}<br>Students: <b>%{x:.2f}</b> / 5<extra></extra>")
            fig.update_layout(
                barmode="group", showlegend=False,
                xaxis=dict(range=[0, 5], gridcolor=h.THEME["chart_grid"], tickfont=dict(color=h.THEME["chart_font"])),
                yaxis=dict(autorange="reversed", automargin=True, tickfont=dict(color=h.THEME["chart_font"])),
                height=110 + 42 * len(per_q), margin=dict(t=10, b=10, l=10, r=10),
                plot_bgcolor=h.THEME["chart_bg"], paper_bgcolor=h.THEME["chart_bg"],
                font=dict(color=h.THEME["chart_font"]),
            )
            h.render_chart_legend([("Auditor", h.PALETTE["series1"]), ("Students", h.PALETTE["series3"])])
            st.plotly_chart(fig, width="stretch", key=f"{key_prefix}_cmp_q")
    with c2:
        with st.container(border=True):
            st.markdown("**By trainer** (biggest gap first)")
            table = pd.DataFrame({
                "Trainer": per_trainer["Trainer"],
                "Centre": per_trainer["Centre"],
                "Auditor avg": per_trainer["audit"].map(lambda v: f"{v:.2f}"),
                "Audits": per_trainer["Audits"].astype(int),
                "Student avg": per_trainer["students"].map(lambda v: f"{v:.2f}"),
                "Student responses": per_trainer["Responses"].astype(int),
                "Gap": per_trainer["Gap"].map(lambda v: f"{v:+.2f}"),
            })
            h.render_wrapped_table(table)
            big = per_trainer[per_trainer["Gap"].abs() >= GAP_FLAG]
            if not big.empty:
                st.caption(
                    f"⚠️ {len(big)} trainer(s) differ by {GAP_FLAG:.0f}+ points between the auditor "
                    "and students. Negative gap = auditor rated lower than students."
                )
            else:
                st.caption("Gap = auditor minus students. Negative means the auditor rated lower.")


def _render_observations(h, key_prefix, df, filt_resp):
    rows = []
    for _, r in filt_resp.sort_values("date", ascending=False).iterrows():
        raw = df.loc[r["_raw_index"]]
        text = _clean(raw.get(COMMENT_COL))
        row = {
            "Date": r["date"], "Centre": r["centre"], "Trainer": r["mentor"],
            "Auditor": r["auditor"], "Course": r["course"], "Batch": r["batch"],
        }
        if TOPIC_COL in df.columns:
            row["Session topic"] = _clean(raw.get(TOPIC_COL))
        row["Overall"] = f"{r['avg']:.2f}"
        row["Observations"] = text or "—"
        rows.append(row)
    if not rows:
        return
    h.section_header("📝", "Auditor observations")
    only_low = st.checkbox("Only show observations scoring below 3/5", key=f"{key_prefix}_obs_low")
    table = pd.DataFrame(rows)
    if only_low:
        table = table[pd.to_numeric(table["Overall"], errors="coerce") < 3]
    if table.empty:
        st.caption("No observations in the current selection.")
        return
    st.download_button(
        "⬇️ Download observations (CSV)",
        data=table.to_csv(index=False).encode("utf-8"),
        file_name=f"{key_prefix}_observations.csv", mime="text/csv",
        key=f"{key_prefix}_obs_download",
    )
    h.render_wrapped_table(table)
