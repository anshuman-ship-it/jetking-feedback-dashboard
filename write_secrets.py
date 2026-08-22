"""
Run once at container startup on Railway (see Procfile) — writes
.streamlit/secrets.toml from environment variables, since Railway has no
direct equivalent of a local secrets.toml file and this file isn't (and
shouldn't be) committed to git.

Set these in Railway's service Variables tab:
  SHEET_URL_TECHNICAL      -> the Technical sheet's Publish-to-web CSV URL
  SHEET_URL_EMPLOYABILITY  -> the Employability sheet's Publish-to-web CSV URL

Safe to run repeatedly (every deploy/restart) — it just overwrites the file
with whatever the current environment variables say.
"""

import os
import pathlib

secrets_dir = pathlib.Path(__file__).parent / ".streamlit"
secrets_dir.mkdir(exist_ok=True)
secrets_path = secrets_dir / "secrets.toml"

technical_url = os.environ.get("SHEET_URL_TECHNICAL", "")
employability_url = os.environ.get("SHEET_URL_EMPLOYABILITY", "")

if not technical_url and not employability_url:
    raise SystemExit(
        "Neither SHEET_URL_TECHNICAL nor SHEET_URL_EMPLOYABILITY is set. "
        "Add them in Railway's service Variables tab before deploying — "
        "see SETUP_GUIDE.md."
    )

lines = ["[sheet_endpoints]"]
if technical_url:
    lines.append(f'technical_url = "{technical_url}"')
if employability_url:
    lines.append(f'employability_url = "{employability_url}"')

secrets_path.write_text("\n".join(lines) + "\n")
print(f"Wrote {secrets_path} from environment variables.")
