# Setup Guide — Jetking Feedback Dashboards (Streamlit)

This app reads live from the Technical and Employability Session Feedback
Google Sheets and renders the same dashboards as the HTML preview, but always
current. It reads each sheet through its **"Publish to web" CSV link** —
just your normal Google account, no Google Cloud project, service account,
or Apps Script needed.

(We tried an Apps Script Web App first, which is normally a fine no-Cloud
option — but Jetking's Google Workspace domain has an admin policy that
requires a signed-in `jetking.com` session for any Apps Script web app,
even ones deployed with "Anyone" access. Publish to web is a different
Google Sheets feature entirely and isn't subject to that policy, which is
why we're using it instead.)

## 1. Publish each Sheet to the web

You'll do this twice — once for the Technical sheet, once for the
Employability sheet.

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

On the machine that will run this (locally for now, or your Railway/Render
service later):

```bash
cd streamlit_app
python -m pip install -r requirements.txt
```

Copy the secrets template and fill in the two URLs from step 1:

```bash
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
```

Edit `.streamlit/secrets.toml`:
```toml
[sheet_endpoints]
technical_url = "https://docs.google.com/spreadsheets/d/e/.../pub?output=csv"      # from step 1
employability_url = "https://docs.google.com/spreadsheets/d/e/.../pub?output=csv"  # from step 1
```

`.gitignore` in this folder already excludes `secrets.toml` from git.

## 3. Run it locally

```bash
streamlit run app.py
```

This opens the app at `http://localhost:8501`. Try both tabs, the Centre and
Mentor filters, and "Refresh data" to confirm it's actually pulling live
values from the sheets.

## 4. Deploy to Railway

Railway runs this as a normal persistent Python process (unlike Vercel,
which can't run Streamlit — Streamlit needs a long-lived server with an
open WebSocket connection, which serverless platforms don't support).

Since Railway has no direct equivalent of a local `secrets.toml` file (and
it shouldn't be committed to git anyway), `write_secrets.py` writes it from
environment variables at container startup, and `Procfile` runs that before
starting Streamlit — both are already in this folder, nothing more to write.

**Steps:**
1. Push this `streamlit_app` folder to a GitHub repo. `.gitignore` already
   excludes `.streamlit/secrets.toml`, `venv/`, and `__pycache__/` — don't
   remove that exclusion.
2. In Railway (railway.app), **New Project → Deploy from GitHub repo** →
   select the repo you just pushed.
3. Railway auto-detects Python from `requirements.txt` and picks up the
   start command from `Procfile` automatically — no manual start command
   needed.
4. In the service's **Variables** tab, add:
   - `SHEET_URL_TECHNICAL` = the Technical sheet's Publish-to-web CSV URL
   - `SHEET_URL_EMPLOYABILITY` = the Employability sheet's Publish-to-web CSV URL

   (Same two URLs currently in your local `secrets.toml` — this is just
   giving Railway the same information a different way.)
5. Deploy. Railway gives you a public `https://<something>.up.railway.app`
   URL once the build finishes — that's the link anyone can open, and it's
   also the base for the per-centre links used by the weekly email reports
   (e.g. `.../?centre=Delhi` opens the dashboard pre-filtered to Delhi).
6. Every push to the connected branch auto-redeploys. If a sheet's URL ever
   changes, update it in the Variables tab and redeploy (or just restart the
   service) rather than editing any file.

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
- **If a question is added to either Google Form later:** the `TECH_Q` /
  `EMP_Q` lists near the top of `app.py` will need a new entry for it, and
  the category split (`cat1`/`cat2`) may need adjusting. Ask me and I'll
  update it.
- **If a sheet's column headers change** (this already happened once —
  "Mentor Name" and "Batch Code" used to have longer two-line header text):
  the `centre_col` / `mentor_col` / `course_col` / `batch_col` values passed
  into `render_dashboard()` near the bottom of `app.py` need to match
  exactly. If a breakdown or filter suddenly shows blank values, this is the
  first thing to check.
