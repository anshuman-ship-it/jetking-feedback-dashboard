# Setup Guide — Jetking Feedback Dashboards (Streamlit)

This app reads live from the Technical, Employability, and Centre
Infrastructure Feedback Google Sheets and renders the same dashboards as the
HTML preview, but always current. It reads each sheet through its **"Publish
to web" CSV link** — just your normal Google account, no Google Cloud
project, service account, or Apps Script needed.

(We tried an Apps Script Web App first, which is normally a fine no-Cloud
option — but Jetking's Google Workspace domain has an admin policy that
requires a signed-in `jetking.com` session for any Apps Script web app,
even ones deployed with "Anyone" access. Publish to web is a different
Google Sheets feature entirely and isn't subject to that policy, which is
why we're using it instead.)

## 1. Publish each Sheet to the web

You'll do this three times — once each for the Technical, Employability, and
Centre Infrastructure sheets.

1. Open the **Technical Session Feedback Form (Responses)** sheet.
2. **File → Share → Publish to web.**
3. In the dialog, use the dropdowns to select the **"Form Responses 1"**
   tab specifically (not "Entire Document"), and set the format to
   **Comma-separated values (.csv)**.
4. Click **Publish**, then confirm the warning dialog.
5. Copy the URL it gives you — it looks like
   `https://docs.google.com/spreadsheets/d/e/2PACX-.../pub?gid=...&single=true&output=csv`.
   This is your `technical_url`.
6. Repeat steps 1–5 for the **Employability Session Feedback Form
   (Responses)** sheet to get your `employability_url`.
7. Repeat steps 1–5 again for the **Centre Infrastructure Feedback Form
   (Responses)** sheet to get your `infrastructure_url`.

**Privacy note:** once published, that URL returns the raw response data
(student names, mentor names, all scores) to anyone who has it — no Google
login required. The URL itself is a long, unguessable ID and isn't
discoverable or indexed, but it isn't access-controlled either. If that's a
concern later, the two more locked-down alternatives are: (a) getting your
Workspace admin to allow anonymous Apps Script web apps, which lets us go
back to the token-gated approach (see `apps_script.gs` in this folder,
kept in case that becomes possible), or (b) a Google Cloud service account,
which authenticates without any browser/session and likely sidesteps this
org policy entirely.

## 2. Install and configure the app

On the machine that will run this (locally for now, or Streamlit Community
Cloud later — see section 4):

```bash
cd streamlit_app
python -m pip install -r requirements.txt
```

Copy the secrets template and fill in the three URLs from step 1:

```bash
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
```

Edit `.streamlit/secrets.toml`:
```toml
[sheet_endpoints]
technical_url = "https://docs.google.com/spreadsheets/d/e/.../pub?output=csv"       # from step 1
employability_url = "https://docs.google.com/spreadsheets/d/e/.../pub?output=csv"   # from step 1
infrastructure_url = "https://docs.google.com/spreadsheets/d/e/.../pub?output=csv"  # from step 1
```

`.gitignore` in this folder already excludes `secrets.toml` from git.

## 3. Run it locally

```bash
streamlit run app.py
```

This opens the app at `http://localhost:8501`. Try all three tabs, the
Centre/Mentor/Course filters, and "Refresh data" to confirm it's actually
pulling live values from the sheets.

## 4. Deploy to Streamlit Community Cloud

Streamlit Community Cloud is free (no card required), built specifically to
host Streamlit apps, and connects straight to a GitHub repo — no Procfile,
no start command, no environment-variable juggling. It has a native secrets
box that accepts TOML directly, which is the same format as your local
`secrets.toml`, so this step is simpler than Railway would have been.

(`Procfile` and `write_secrets.py` in this folder were built for Railway and
aren't used by Streamlit Cloud — safe to ignore or delete, Streamlit Cloud
just won't touch them.)

**Steps:**
1. Push this `streamlit_app` folder to a GitHub repo (already done —
   `jetking-feedback-dashboard` on your GitHub account). `.gitignore`
   already excludes `.streamlit/secrets.toml`, `venv/`, and `__pycache__/` —
   don't remove that exclusion.
2. Go to **share.streamlit.io** and sign in with your GitHub account (the
   same one the repo lives on).
3. Click **New app**. Choose:
   - **Repository:** `anshuman-ship-it/jetking-feedback-dashboard`
   - **Branch:** `main`
   - **Main file path:** `app.py`
4. Before clicking Deploy, open **Advanced settings → Secrets** and paste
   the same content as your local `.streamlit/secrets.toml`:
   ```toml
   [sheet_endpoints]
   technical_url = "https://docs.google.com/spreadsheets/d/e/.../pub?output=csv"
   employability_url = "https://docs.google.com/spreadsheets/d/e/.../pub?output=csv"
   infrastructure_url = "https://docs.google.com/spreadsheets/d/e/.../pub?output=csv"
   ```
5. Click **Deploy**. First build takes a couple of minutes. You'll get a
   public URL like `https://jetking-feedback-dashboard.streamlit.app` (exact
   name depends on availability, editable in app settings) — that's the link
   anyone can open, and it's also the base for the per-centre links used by
   the weekly email reports (e.g. `.../?centre=Delhi` opens the dashboard
   pre-filtered to Delhi).
6. Every push to `main` auto-redeploys. If a sheet's URL ever changes,
   update it under the app's **Settings → Secrets** in the Streamlit Cloud
   dashboard and it'll pick it up on the next rerun — no redeploy needed.

## Notes

- **Data freshness:** the app caches each sheet's data for 5 minutes so it
  doesn't refetch the CSV on every click. Anyone viewing can also click
  "Refresh data" for an immediate pull.
- **Access control:** as set up here, anyone who can reach the deployed
  app's URL can view the dashboard — there's no login screen, and (per the
  privacy note above) the underlying data URLs themselves are also
  unauthenticated. If you need to restrict who can see the app, that's
  usually handled at the network level (an internal-only URL, or a reverse
  proxy with basic auth) — let me know if you want that added once we
  deploy.
- **If a question is added to any Google Form later:** the `TECH_Q` /
  `EMP_Q` / `INFRA_Q` lists (and `INFRA_SPECIAL_Q` for Infrastructure's
  Yes/No questions) near the top of `app.py` will need a new entry for it,
  and the category split (`cat1`/`cat2`) may need adjusting. Ask me and I'll
  update it.
- **If a sheet's column headers change** (this has already happened twice —
  once when "Mentor Name" and "Batch Code" lost their longer two-line header
  text, and again when the Technical and Employability forms' mentor/batch
  headers drifted and effectively swapped): the `centre_col` / `mentor_col` /
  `course_col` / `batch_col` values passed into `render_dashboard()` (or
  `centre_col`/`course_col`/`batch_col` into `render_infrastructure_dashboard()`)
  near the bottom of `app.py` need to match exactly. If a breakdown or filter
  suddenly shows blank values, this is the first thing to check — ask me to
  re-read the live sheet's actual header row via the Google Drive connector
  rather than guessing.
- **The Centre Infrastructure sheet has a stray extra column** ("Column 15",
  no header text) as of when it was created — likely a leftover from the
  form being edited. Harmless: the app doesn't reference it, so it's ignored.
  Worth deleting from the sheet if you want to tidy it up, but not urgent.
