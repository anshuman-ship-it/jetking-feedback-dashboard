"""
reset_password.py — generate the TOML block for a new or reset password.

Run this locally (from the streamlit_app folder, same place as secrets.toml)
whenever you need to:
  - reset an existing person's password, or
  - add a brand-new centre/admin login.

It does NOT edit secrets.toml for you — it only prints the exact block to
paste in. That's deliberate: secrets.toml has comments and formatting worth
keeping, and this avoids any risk of a script mangling it. You'll paste the
printed block into TWO places:
  1. Your local .streamlit/secrets.toml (replacing that person's existing
     [credentials.usernames."..."] block, or adding a new one)
  2. The Streamlit Community Cloud app's Settings -> Secrets (same edit)

Usage:
    python reset_password.py

You'll be prompted for the person's email, their new password (typed twice,
hidden), and — only if this script can't find them already in your local
secrets.toml — their name and role.
"""

import getpass
import pathlib
import sys

try:
    import streamlit_authenticator as stauth
except ImportError:
    print("streamlit_authenticator isn't installed in this environment.")
    print("Run: python -m pip install -r requirements.txt")
    sys.exit(1)

SECRETS_PATH = pathlib.Path(__file__).parent / ".streamlit" / "secrets.toml"

CENTRES = ["Dadar", "Vashi", "Laxminagar", "Maninagar", "Bhawaniopore", "Khar"]


def load_existing_user(email):
    """Best-effort lookup of an existing user's name/roles from secrets.toml,
    so a password reset doesn't require re-typing them. Returns None if the
    file can't be read/parsed or the email isn't found — the caller falls
    back to asking interactively either way, so this is a convenience only,
    never a hard requirement."""
    try:
        try:
            import tomllib  # Python 3.11+
            data = tomllib.loads(SECRETS_PATH.read_text(encoding="utf-8"))
        except ImportError:
            import toml  # fallback if tomllib isn't available
            data = toml.loads(SECRETS_PATH.read_text(encoding="utf-8"))
        return data["credentials"]["usernames"].get(email)
    except Exception:
        return None


def prompt_password():
    while True:
        pw1 = getpass.getpass("New password (hidden while typing): ")
        if len(pw1) < 8:
            print("  Please use at least 8 characters. Try again.\n")
            continue
        pw2 = getpass.getpass("Type it again to confirm: ")
        if pw1 != pw2:
            print("  Those didn't match — try again.\n")
            continue
        return pw1


def prompt_role():
    print("\nIs this an admin account (sees every centre), or a centre login")
    print("(locked to just one centre)?")
    choice = input("Type 'admin' or a centre name (Dadar/Vashi/Laxminagar/"
                    "Maninagar/Bhawaniopore/Khar): ").strip()
    if choice.lower() == "admin":
        return ["admin"]
    matches = [c for c in CENTRES if c.lower() == choice.lower()]
    if not matches:
        print(f"  '{choice}' isn't one of the recognized centre names — "
              f"defaulting to no role. You can fix the `roles` line by hand "
              f"in the block below before pasting it in.")
        return []
    return [f"centre:{matches[0]}"]


def main():
    print("=" * 70)
    print("Jetking Feedback Dashboard — password reset / new account helper")
    print("=" * 70)

    email = input("\nEmail address (this is also their login username): ").strip()
    if not email or "@" not in email:
        print("That doesn't look like an email address — aborting.")
        sys.exit(1)

    existing = load_existing_user(email)
    if existing:
        name = existing.get("name", email)
        roles = existing.get("roles", [])
        print(f"\nFound an existing account for {email}:")
        print(f"  Name:  {name}")
        print(f"  Roles: {roles}")
        print("Reusing these — this will just be a password reset.")
    else:
        print(f"\nNo existing account found for {email} in your local "
              f"secrets.toml (or it couldn't be read) — treating this as "
              f"a new account.")
        name = input("Full name to display (e.g. 'Khar Centre Manager'): ").strip()
        roles = prompt_role()

    password = prompt_password()
    password_hash = stauth.Hasher.hash(password)

    print("\n" + "=" * 70)
    print("Paste this block into BOTH:")
    print("  1. Your local .streamlit/secrets.toml, replacing any existing")
    print(f'     [credentials.usernames."{email}"] block for this person')
    print("  2. The Streamlit Community Cloud app's Settings -> Secrets")
    print("=" * 70 + "\n")

    roles_toml = "[" + ", ".join(f'"{r}"' for r in roles) + "]"
    print(f'[credentials.usernames."{email}"]')
    print(f'email = "{email}"')
    print(f'name = "{name}"')
    print(f'password = "{password_hash}"')
    print(f'roles = {roles_toml}')
    print()
    print("Then tell them their new password directly (it's not saved "
          "anywhere by this script) — and don't paste the plaintext "
          "password anywhere that gets committed to git.")


if __name__ == "__main__":
    main()
